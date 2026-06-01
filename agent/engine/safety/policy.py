"""
Safety Policy — Phase 3 / 17단계

LLM 이 생성한 코드를 sandbox 실행 전에 정적 분석으로 차단한다.

- AST 파싱으로 **BLOCKED_IMPORTS** 탐지 (`os`, `subprocess`, `socket` 등)
- 위험 호출 **BLOCKED_CALLS** 탐지 (`eval`, `exec`, `__import__`, `open` 등 —
  이름 호출 `eval(...)` 과 속성 호출 `x.system(...)` 모두)
- 인트로스펙션 우회 **BLOCKED_DUNDERS** 탐지 (`__subclasses__`, `__globals__` 등 —
  import 없이 위험 모듈에 도달하는 sandbox 탈출 차단)
- 추가 의심 패턴 **WARN_PATTERNS** 탐지 (medium-risk)
- 위험도 분류: low / medium / high

정책 (`decide()` / `evaluate()`):
  - high   → 'block'   : 즉시 거부 (passed=False), sandbox 진입 불가
  - medium → 'approve' : 사용자 승인 게이트 (선택). `BLOCK_MEDIUM=True` 면 'block'
  - low    → 'allow'   : 통과

`creation_pipeline._test_agent` 와 (앞으로) `sandbox/runner`(18단계) 진입 전에
호출된다. 통과(passed=True)한 코드만 다음 단계로 진입한다.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Literal

RiskLevel = Literal["low", "medium", "high"]


# ──────────────────────────────────────────────────────────
# 차단 목록 — high-risk (즉시 거부)
# ──────────────────────────────────────────────────────────

# 운영체제·네트워크·동적 import·직렬화 등 LLM 생성 도구에 절대 허용 안 함
BLOCKED_IMPORTS: set[str] = {
    "os", "sys", "subprocess", "socket", "requests", "urllib", "http",
    "shutil", "pathlib", "ctypes", "importlib", "multiprocessing",
    "threading", "asyncio", "telnetlib", "ftplib", "smtplib",
    "pickle", "marshal", "shelve", "tempfile",
}

# 코드 실행·동적 평가·파일 IO 직접 호출
BLOCKED_CALLS: set[str] = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "breakpoint",
}

# 속성 호출로 들어오는 위험 메서드 — high
# (`x.system(...)` / `x.popen(...)` 등. 모듈 import 차단을 우회해 전달된 객체로
#  OS 명령을 실행하는 경우를 잡는다. pandas/numpy/list/dict API 와 충돌하지 않는
#  이름만 넣는다.)
BLOCKED_ATTR_CALLS: set[str] = {
    "system", "popen", "spawn", "spawnl", "spawnv", "fork", "execv", "execve",
}

# 인트로스펙션 기반 sandbox 탈출용 dunder 속성 — high
# (`().__class__.__bases__[0].__subclasses__()` 식으로 import 없이 os 에 도달하는
#  고전적 우회를 차단한다. 정상적인 분석 코드는 이 속성들을 쓸 일이 없다.)
BLOCKED_DUNDERS: set[str] = {
    "__subclasses__", "__bases__", "__base__", "__mro__",
    "__globals__", "__builtins__", "__import__", "__loader__",
    "__getattribute__", "__dict__", "__code__", "__closure__",
}


# ──────────────────────────────────────────────────────────
# 경고 패턴 — medium-risk (현재는 차단하지 않고 플래그만)
# ──────────────────────────────────────────────────────────

WARN_PATTERNS: list[tuple[str, str]] = [
    (r"\bpd\.read_(csv|excel|parquet|json|table|html)",
     "pandas 직접 IO — loader.read_file 사용 권장"),
    (r"\bpandas\.read_(csv|excel|parquet|json|table|html)",
     "pandas 직접 IO — loader.read_file 사용 권장"),
    (r"\bcsv\.(reader|DictReader|writer)", "csv 모듈 직접 사용"),
    (r"\b(setattr|getattr|delattr)\s*\(\s*[a-zA-Z_]+\s*,\s*['\"]_",
     "이중 언더스코어 속성 동적 조작"),
]


# ──────────────────────────────────────────────────────────
# 결과 타입
# ──────────────────────────────────────────────────────────


@dataclass
class Violation:
    rule: str       # 'BLOCKED_IMPORT' | 'BLOCKED_CALL' | 'WARN_PATTERN' | 'SYNTAX'
    message: str    # 사람이 읽을 위반 내용
    risk: RiskLevel


@dataclass
class PolicyResult:
    passed: bool
    risk_level: RiskLevel
    violations: list[Violation] = field(default_factory=list)

    def block_messages(self) -> list[str]:
        return [v.message for v in self.violations if v.risk == "high"]

    def warn_messages(self) -> list[str]:
        return [v.message for v in self.violations if v.risk == "medium"]

    def summary(self) -> str:
        if not self.violations:
            return "위반 없음 (low)"
        head = f"위험도: {self.risk_level}"
        body = "\n".join(
            f"  - [{v.risk}] {v.rule}: {v.message}" for v in self.violations
        )
        return f"{head}\n{body}"


# ──────────────────────────────────────────────────────────
# 위험도 계산
# ──────────────────────────────────────────────────────────


def _aggregate_risk(violations: list[Violation]) -> RiskLevel:
    if any(v.risk == "high" for v in violations):
        return "high"
    if any(v.risk == "medium" for v in violations):
        return "medium"
    return "low"


# ──────────────────────────────────────────────────────────
# 메인 검사
# ──────────────────────────────────────────────────────────


def check_code(code: str) -> PolicyResult:
    """LLM 생성 코드를 정적 분석으로 검사한다."""
    violations: list[Violation] = []

    # 1) AST 파싱 — 자체 실패는 high 로 분류
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return PolicyResult(
            passed=False,
            risk_level="high",
            violations=[Violation("SYNTAX", f"문법 오류: {e}", "high")],
        )

    # 2) BLOCKED_IMPORTS — high
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in BLOCKED_IMPORTS:
                    violations.append(
                        Violation("BLOCKED_IMPORT", f"import {alias.name}", "high")
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in BLOCKED_IMPORTS:
                    violations.append(
                        Violation(
                            "BLOCKED_IMPORT",
                            f"from {node.module} import ...",
                            "high",
                        )
                    )

    # 3) BLOCKED_CALLS — high (이름 호출 `eval(...)` + 속성 호출 `x.system(...)`)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
                violations.append(
                    Violation("BLOCKED_CALL", f"{node.func.id}(...)", "high")
                )
            elif isinstance(node.func, ast.Attribute) and (
                node.func.attr in BLOCKED_CALLS
                or node.func.attr in BLOCKED_ATTR_CALLS
            ):
                violations.append(
                    Violation("BLOCKED_CALL", f".{node.func.attr}(...)", "high")
                )

    # 4) BLOCKED_DUNDERS — high (인트로스펙션 기반 우회 차단)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in BLOCKED_DUNDERS:
            violations.append(
                Violation("BLOCKED_DUNDER", f".{node.attr}", "high")
            )

    # 5) WARN_PATTERNS — medium
    for pattern, message in WARN_PATTERNS:
        if re.search(pattern, code):
            violations.append(Violation("WARN_PATTERN", message, "medium"))

    risk = _aggregate_risk(violations) if violations else "low"
    passed = risk != "high"
    return PolicyResult(passed=passed, risk_level=risk, violations=violations)


# ──────────────────────────────────────────────────────────
# 위험도 기반 결정 게이트 (17단계 정책)
#   - high   → 'block'   : 즉시 거부, sandbox 진입 불가
#   - medium → 'approve' : 사용자 승인 게이트 (선택). BLOCK_MEDIUM=True 면 'block'
#   - low    → 'allow'   : 통과
# ──────────────────────────────────────────────────────────

Action = Literal["allow", "approve", "block"]

# medium 위험을 사용자 승인 없이 즉시 차단할지 여부.
# 기본값 False — medium 은 경고와 함께 통과시키고 다음 단계(sandbox)에서 격리한다.
BLOCK_MEDIUM: bool = False


def decide(result: PolicyResult, block_medium: bool = BLOCK_MEDIUM) -> Action:
    """PolicyResult 를 실행 결정(allow / approve / block)으로 변환한다."""
    if result.risk_level == "high":
        return "block"
    if result.risk_level == "medium":
        return "block" if block_medium else "approve"
    return "allow"


def evaluate(code: str, block_medium: bool = BLOCK_MEDIUM) -> tuple[Action, PolicyResult]:
    """코드를 검사하고 (결정, 상세결과) 를 함께 반환하는 편의 함수."""
    result = check_code(code)
    return decide(result, block_medium=block_medium), result
