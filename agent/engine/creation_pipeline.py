"""
Creation Pipeline — Phase 3 / 15단계

5 서브에이전트 체인:
  Spec      → 사용자 의도를 구조화된 사양으로 변환 (DEFAULT_MODEL)
  Coder     → 사양에서 Python 함수 코드 생성 (CODER_MODEL = qwen3-coder:30b)
  Test      → 최소 정적 검사 + 컴파일 (15단계 한정; production sandbox 는 18단계)
  Register  → engine/tools/generated/ 저장 + registry.json 갱신
  ReviewFix → 실패 시 오류·사유 피드백 후 Coder 재호출 (최대 N회)

⚠️ Phase 3 안전 원칙:
  - Test 는 AST 정적 검사 + 안전 정책 + 컴파일만 수행한다. 실제 함수 호출은 안 한다.
  - 17단계(완료): safety/policy.py 가 BLOCKED_IMPORTS / CALLS / DUNDERS 와 위험도
    분류를 수행 — high 위험 코드는 등록 전에 차단된다 (`_test_agent` 가 위임).
  - 18단계(완료): sandbox/runner.py 가 별도 subprocess + 모의 loader 주입 +
    timeout/메모리 상한으로 격리 실행 검증 (`_sandbox_agent`). 본체 프로세스는
    어떤 경우에도 영향받지 않으며, 런타임 크래시/폭주 코드는 등록 전에 걸러진다.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Optional

from agent.llm_factory import get_llm
from config.settings import settings

# ──────────────────────────────────────────────────────────
# Pending intent — orchestrator 가 가로채는 용도 (14단계 유지)
# ──────────────────────────────────────────────────────────

_pending_intent: Optional[str] = None


def set_pending(intent: str) -> None:
    global _pending_intent
    _pending_intent = intent


def get_pending() -> Optional[str]:
    return _pending_intent


def reset_pending() -> None:
    global _pending_intent
    _pending_intent = None


# ──────────────────────────────────────────────────────────
# 감사 로그
# ──────────────────────────────────────────────────────────

_audit_log: list[tuple[str, str]] = []


def _log(step: str, content: str) -> None:
    _audit_log.append((step, content))


def get_audit_log() -> list[tuple[str, str]]:
    return list(_audit_log)


def _reset_log() -> None:
    _audit_log.clear()


# ──────────────────────────────────────────────────────────
# 파싱 헬퍼
# ──────────────────────────────────────────────────────────


def _strip_code_block(text: str) -> str:
    """LLM 출력에서 ```python ... ``` 또는 ```json ... ``` 펜스를 떼낸다."""
    text = text.strip()
    fence = re.match(r"^```(?:python|json)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_json(text: str) -> dict:
    """LLM 출력에서 JSON 객체를 추출 (펜스 처리 + 첫 { ~ 마지막 })."""
    cleaned = _strip_code_block(text)
    try:
        return json.loads(cleaned)
    except Exception:
        # 첫 { 부터 매칭되는 }까지 추출 시도
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _safe_name(name: str) -> str:
    """Python identifier 안전 변환 (소문자 스네이크)."""
    s = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip())
    s = re.sub(r"_+", "_", s).strip("_").lower()
    if not s or not s[0].isalpha():
        s = "tool_" + s
    return s


# ──────────────────────────────────────────────────────────
# Sub-agent 1: Spec
# ──────────────────────────────────────────────────────────

_SPEC_PROMPT = """다음 사용자 의도를 분석해 새 도구의 사양을 JSON 으로 작성하라.

[사용자 의도]
{user_intent}

[출력 JSON 스키마]
{{
  "name": "<소문자 스네이크 함수명>",
  "description": "<한 문장 설명>",
  "inputs": [
    {{"name": "<인자명>", "type": "<str|int|float|list[str]|...>", "desc": "<설명>"}}
  ],
  "output": "<반환 형태 자연어 설명>",
  "logic": "<처리 절차 자연어 설명>"
}}

[제약]
- 데이터 접근은 file_manager / loader 만 사용한다고 가정.
- 결과를 파일로 저장하는 도구라면 inputs 에 filename 류 포함.
- 차트·이미지 생성은 matplotlib 가능하다고 가정 (단 15단계 검증은 정적 검사만).

JSON 만 출력하라 (다른 설명·주석 금지):"""


def _spec_agent(user_intent: str) -> dict:
    """user_intent → 구조화된 도구 사양 dict"""
    llm = get_llm("ollama", settings.DEFAULT_MODEL, temperature=0.0)
    resp = llm.invoke(_SPEC_PROMPT.format(user_intent=user_intent))
    raw = getattr(resp, "content", str(resp))
    spec = _extract_json(raw)
    spec["name"] = _safe_name(spec.get("name", "generated_tool"))
    spec.setdefault("description", "")
    spec.setdefault("inputs", [])
    spec.setdefault("output", "")
    spec.setdefault("logic", "")
    return spec


# ──────────────────────────────────────────────────────────
# Sub-agent 2: Coder — 표준 스캐폴드 + 컴플라이언스 게이트 (16단계)
# ──────────────────────────────────────────────────────────


def _build_scaffold(spec: dict) -> str:
    """
    사양으로부터 표준 스캐폴드(템플릿)를 만든다.
    Coder 는 import / 데코레이터 / 시그니처를 그대로 두고 본문만 채운다.
    """
    inputs = spec.get("inputs", [])
    params = []
    for inp in inputs:
        name = inp.get("name", "arg")
        typ = inp.get("type", "str")
        params.append(f"{name}: {typ}")
    sig = ", ".join(params) if params else ""
    fn_name = spec["name"]
    desc = (spec.get("description") or "").replace('"""', '"')

    return (
        "from langchain_core.tools import tool\n"
        "\n"
        "# 데이터 접근은 반드시 loader 경유 (직접 IO 금지)\n"
        "from agent.engine.data import loader\n"
        "# 결과 파일을 생성하는 경우만 사용\n"
        "# from agent.engine.tools._helpers import save_xlsx_and_register\n"
        "\n"
        "\n"
        "@tool\n"
        f'def {fn_name}({sig}) -> str:\n'
        f'    """{desc}"""\n'
        "    # ── AI: 여기에 분석 로직만 작성 ──\n"
        "    # - 데이터 접근:   loader.read_file(filename), loader.list_files()\n"
        "    # - 결과 파일:    save_xlsx_and_register(df, prefix=\"...\")\n"
        "    # - 직접 IO(open, pd.read_csv 등) 금지\n"
        "    # - 실패 시 한국어 에러 문자열을 return\n"
        "    pass\n"
    )


_CODER_PROMPT = """다음 사양에 맞춰, 아래 **표준 스캐폴드**를 채워 Python 함수를 작성하라.

[사양]
{spec_json}

[표준 스캐폴드 — import / @tool / 시그니처를 변경하지 말고 본문만 실제 구현으로 교체]
```python
{scaffold}
```

[엄격한 규칙 — 위반 시 컴플라이언스 게이트에서 거부됨]
1. 스캐폴드의 `from langchain_core.tools import tool` 임포트와 `@tool` 데코레이터를 유지하라.
2. 함수명 `{name}` 과 사양의 inputs 시그니처를 정확히 따르라.
3. 데이터 접근은 **반드시 `loader.read_file(filename)` / `loader.list_files()`** 사용.
4. `open()`, `pd.read_csv`, `pandas.read_*`, `csv.reader` 등 **직접 IO 금지**.
5. 금지 임포트: `os`, `sys`, `subprocess`, `socket`, `requests`, `urllib`,
   `shutil`, `pathlib`, `ctypes`, `importlib`.
6. 금지 호출: `eval`, `exec`, `compile`, `__import__`.
7. **AI 는 분석 로직만 작성** — 데이터 접근·결과 저장은 헬퍼만 사용.
8. pandas / numpy 만 자유롭게 추가 import 가능 (numpy 필요 시 한 줄 추가).
9. 결과 파일이 필요한 경우만 `save_xlsx_and_register(df, prefix="...")` 사용.
10. 실패 케이스는 한국어 에러 문자열로 `return`.

[출력]
순수 Python 코드만 ```python ... ``` 블록으로 출력. 설명·주석 외 텍스트 금지.

{feedback_block}"""


def _coder_agent(spec: dict, feedback: Optional[str]) -> str:
    """사양 + (있으면) 이전 시도 피드백 → 스캐폴드를 채운 함수 코드"""
    coder_model = settings.CODER_MODEL or settings.DEFAULT_MODEL
    llm = get_llm("ollama", coder_model, temperature=0.1)
    scaffold = _build_scaffold(spec)
    feedback_block = (
        f"[이전 시도 피드백 — 반드시 해결]\n{feedback}" if feedback else ""
    )
    prompt = _CODER_PROMPT.format(
        spec_json=json.dumps(spec, ensure_ascii=False, indent=2),
        scaffold=scaffold,
        name=spec["name"],
        feedback_block=feedback_block,
    )
    resp = llm.invoke(prompt)
    raw = getattr(resp, "content", str(resp))
    return _strip_code_block(raw)


# ──────────────────────────────────────────────────────────
# Compliance Gate — Coder 출력 스캐폴드 규칙 검증 (16단계)
#   17단계 safety/policy.py 와는 분리: 여기서는 "스캐폴드 형태" 만 검사.
# ──────────────────────────────────────────────────────────

_FORBIDDEN_IO_PATTERNS = [
    (r"\bopen\s*\(", "open() 직접 호출 — loader.read_file 사용"),
    (r"\bpd\.read_", "pd.read_* 직접 호출 — loader.read_file 사용"),
    (r"\bpandas\.read_", "pandas.read_* 직접 호출 — loader.read_file 사용"),
    (r"\bcsv\.reader", "csv.reader 직접 사용 금지"),
    (r"\bcsv\.DictReader", "csv.DictReader 직접 사용 금지"),
]


def _compliance_agent(code: str, spec: dict) -> tuple[bool, str]:
    """스캐폴드 컴플라이언스 검사 — Coder 가 표준 시그니처/IO 규칙을 따랐는지."""
    # 1. 필수 langchain_core import
    if not re.search(
        r"^\s*from\s+langchain_core\.tools\s+import\s+tool",
        code,
        re.MULTILINE,
    ):
        return False, "필수 import 누락: from langchain_core.tools import tool"

    # 2. @tool 데코레이터
    if not re.search(r"^\s*@tool\s*(\(.*\))?\s*$", code, re.MULTILINE):
        return False, "@tool 데코레이터 누락"

    # 3. 함수명 일치
    fn_name = spec["name"]
    if not re.search(rf"^\s*def\s+{re.escape(fn_name)}\s*\(", code, re.MULTILINE):
        return False, f"함수 '{fn_name}' 미정의 또는 시그니처 불일치"

    # 4. 직접 IO 패턴 차단
    for pattern, reason in _FORBIDDEN_IO_PATTERNS:
        if re.search(pattern, code):
            return False, f"직접 IO 사용 — {reason}"

    # 5. 본문이 pass 만 남아있지 않은지 (구현 미완)
    func_body_match = re.search(
        rf"def\s+{re.escape(fn_name)}\s*\([^)]*\)\s*->[^:]*:(.*?)(?=\Z|^\S)",
        code,
        re.DOTALL | re.MULTILINE,
    )
    if func_body_match:
        body = func_body_match.group(1)
        non_comment = "\n".join(
            line for line in body.split("\n")
            if line.strip() and not line.strip().startswith("#")
            and '"""' not in line
        )
        if non_comment.strip() in {"pass", ""}:
            return False, "함수 본문 미구현 — pass / 빈 본문"

    return True, "스캐폴드 컴플라이언스 통과"


# ──────────────────────────────────────────────────────────
# Sub-agent 3: Test (안전 정책 + 정적 검사 + 컴파일)
#   - 17단계에서 BLOCKED_IMPORTS / BLOCKED_CALLS / 위험도 분류는
#     agent/engine/safety/policy.py 로 위임.
#   - 실제 함수 호출은 18단계 sandbox 에서.
# ──────────────────────────────────────────────────────────


def _test_agent(code: str, spec: dict) -> tuple[bool, str]:
    """안전 정책 + 정적 검사 + 컴파일. 실제 호출은 18단계 sandbox 에서."""
    from agent.engine.safety import policy

    # 1) AST 파싱
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"문법 오류: {e}"

    # 2) 안전 정책 검사 (17단계 — safety/policy.py 위임)
    pol = policy.check_code(code)
    if not pol.passed:
        blocks = ", ".join(pol.block_messages())
        return False, f"안전 정책 위반 (위험도={pol.risk_level}): {blocks}"

    # 3) 함수 정의 존재 확인
    target = spec["name"]
    func_names = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
    }
    if target not in func_names:
        return False, (
            f"사양의 함수 '{target}' 가 코드에 없음 (정의된 함수: {func_names})"
        )

    # 4) 컴파일
    try:
        compile(code, f"<generated:{target}>", "exec")
    except Exception as e:
        return False, f"컴파일 실패: {e}"

    msg = f"통과 (위험도={pol.risk_level})"
    if pol.risk_level == "medium":
        warns = ", ".join(pol.warn_messages())
        msg += f" — medium 경고: {warns}"
    return True, msg


# ──────────────────────────────────────────────────────────
# Sub-agent 3.5: Sandbox (18단계 — 격리 subprocess 실제 실행)
#   - 안전 정책(17)을 통과한 코드를 메인 앱과 분리된 subprocess 에서 한 번 실행.
#   - 모의 loader/helpers 주입으로 실제 업로드 파일에 접근하지 못한다.
#   - 런타임에 터지거나(예외) 폭주(timeout)하는 코드를 등록 전에 걸러낸다.
# ──────────────────────────────────────────────────────────


def _sandbox_agent(code: str, spec: dict) -> tuple[bool, str]:
    """generated 도구를 격리 sandbox 에서 smoke 실행한다."""
    from agent.engine.sandbox import runner

    result = runner.smoke_test(code, spec)
    return result.success, result.summary()


# ──────────────────────────────────────────────────────────
# Sub-agent 4: Register
# ──────────────────────────────────────────────────────────


def _register_agent(code: str, spec: dict) -> str:
    """generated 도구 등록 — 19단계: manager 가 영속 변경 단일 게이트웨이."""
    from agent.engine.tool_registry import manager

    path = manager.register(spec["name"], spec.get("description", ""), code)
    try:
        return str(Path(path).relative_to(settings.BASE_DIR))
    except ValueError:
        return path


# ──────────────────────────────────────────────────────────
# Pipeline 진입점
# ──────────────────────────────────────────────────────────


_MAX_RETRIES = 2  # ReviewFix 루프 최대 시도 횟수


def run(user_intent: str) -> str:
    """
    Creation Pipeline 진입점.
    Spec → (Coder → Test [→ ReviewFix])* → Register 순으로 실행.

    Phase 3 / 15단계 — 파이프라인 골격 완성. 실제 실행 검증은 18단계 sandbox 에서.
    """
    _reset_log()
    _log("Pipeline 시작", user_intent)

    # 1) Spec
    try:
        spec = _spec_agent(user_intent)
        _log("Spec", json.dumps(spec, ensure_ascii=False))
    except Exception as e:
        _log("Spec 실패", str(e))
        return _failure_msg(f"Spec 단계 실패: {e}")

    # 1.5) 재사용 가드 (19단계) — 유사한 generated 도구가 이미 있으면 재생성 안 함
    reusable = _find_reusable(spec)
    if reusable is not None:
        _log("Reuse", f"{reusable['name']} (v{reusable.get('version', 1)})")
        return _reuse_msg(reusable)

    # 2) Coder → Compliance → Test → (ReviewFix → Coder)*
    feedback: Optional[str] = None
    code = ""
    last_reason = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            code = _coder_agent(spec, feedback)
            _log(f"Coder (시도 {attempt + 1})", code[:300])
        except Exception as e:
            _log("Coder 실패", str(e))
            return _failure_msg(f"Coder 단계 실패: {e}")

        # 16단계 — 스캐폴드 컴플라이언스 게이트
        compliant, comp_reason = _compliance_agent(code, spec)
        _log(f"Compliance (시도 {attempt + 1})", comp_reason)
        if not compliant:
            last_reason = f"Compliance: {comp_reason}"
            feedback = (
                f"이전 코드가 스캐폴드 규칙 위반: {comp_reason}\n"
                "표준 스캐폴드를 정확히 따라 다시 작성하라."
            )
            _log(f"ReviewFix (시도 {attempt + 1})", last_reason)
            continue

        # 15·17단계 — 안전 정책 + 정적 검사 + 컴파일
        passed, test_reason = _test_agent(code, spec)
        _log(f"Test (시도 {attempt + 1})", test_reason)
        if not passed:
            last_reason = f"Test: {test_reason}"
            feedback = (
                f"이전 코드가 다음 사유로 검증 실패: {test_reason}\n"
                "반드시 이 문제를 해결한 코드를 다시 작성하라."
            )
            _log(f"ReviewFix (시도 {attempt + 1})", last_reason)
            continue

        # 18단계 — sandbox 격리 실행 검증 (별도 subprocess + 모의 loader)
        sb_passed, sb_reason = _sandbox_agent(code, spec)
        _log(f"Sandbox (시도 {attempt + 1})", sb_reason)
        if sb_passed:
            break

        last_reason = f"Sandbox: {sb_reason}"
        feedback = (
            f"이전 코드가 sandbox 격리 실행에서 실패: {sb_reason}\n"
            "예외 없이 끝까지 실행되도록 하고, 실패 케이스는 raise 대신 "
            "한국어 에러 문자열을 return 하라."
        )
        _log(f"ReviewFix (시도 {attempt + 1})", last_reason)
    else:
        return _failure_msg(
            f"최대 재시도 {_MAX_RETRIES}회 초과 — 마지막 사유: {last_reason}"
        )

    # 3) Register
    try:
        registered_path = _register_agent(code, spec)
        _log("Register", registered_path)
    except Exception as e:
        _log("Register 실패", str(e))
        return _failure_msg(f"Register 단계 실패: {e}")

    return _success_msg(spec, registered_path)


# ──────────────────────────────────────────────────────────
# 재사용 가드 (19단계) — 유사한 generated 도구 탐지
# ──────────────────────────────────────────────────────────


def _find_reusable(spec: dict) -> Optional[dict]:
    """spec 과 유사한 기존 generated 도구가 있으면 그 메타를 반환한다.

    - 이름이 정확히 같으면 즉시 재사용 후보.
    - 아니면 description/logic 키워드가 충분히 겹치는 generated 도구를 찾는다
      (오탐을 줄이기 위해 보수적으로 임계값 적용).
    """
    from agent.engine.tool_registry import manager

    name = spec.get("name", "")
    exact = manager.find(name)
    if exact is not None and exact.get("source") == "generated":
        return exact

    query = f"{spec.get('description', '')} {spec.get('logic', '')}".strip()
    words = {w for w in query.lower().split() if len(w) >= 2}
    if not words:
        return None

    candidates = manager.search(query, source="generated")
    if not candidates:
        return None

    top = candidates[0]
    haystack = (top.get("name", "") + " " + top.get("description", "")).lower()
    overlap = sum(1 for w in words if w in haystack)
    if overlap >= max(2, int(len(words) * 0.5)):
        return top
    return None


# ──────────────────────────────────────────────────────────
# refactor_tool (19단계) — 기존 generated 도구를 LLM 으로 개선 (version +1)
# ──────────────────────────────────────────────────────────


_REFACTOR_PROMPT = """다음은 기존에 자동 생성된 도구의 코드다. 아래 [개선 지시]를
반영해 **같은 함수명·시그니처를 유지한 채** 코드를 다시 작성하라.

[기존 코드]
```python
{old_code}
```

[개선 지시]
{instruction}

[엄격한 규칙 — 위반 시 거부됨]
1. `from langchain_core.tools import tool` 임포트와 `@tool` 데코레이터를 유지하라.
2. 함수명 `{name}` 을 변경하지 말라.
3. 데이터 접근은 반드시 `loader.read_file()` / `loader.list_files()` 사용.
4. `open()`, `pd.read_*`, `csv.reader` 등 직접 IO 금지.
5. 금지 임포트: `os`, `sys`, `subprocess`, `socket`, `requests`, `urllib`,
   `shutil`, `pathlib`, `ctypes`, `importlib`.
6. 금지 호출: `eval`, `exec`, `compile`, `__import__`.
7. 실패 케이스는 raise 대신 한국어 에러 문자열로 `return`.

[출력]
순수 Python 코드만 ```python ... ``` 블록으로 출력.

{feedback_block}"""


def _refactor_coder(old_code: str, instruction: str, name: str,
                    feedback: Optional[str]) -> str:
    """기존 코드 + 개선 지시 → 개선된 함수 코드."""
    coder_model = settings.CODER_MODEL or settings.DEFAULT_MODEL
    llm = get_llm("ollama", coder_model, temperature=0.1)
    feedback_block = (
        f"[이전 시도 피드백 — 반드시 해결]\n{feedback}" if feedback else ""
    )
    prompt = _REFACTOR_PROMPT.format(
        old_code=old_code,
        instruction=instruction,
        name=name,
        feedback_block=feedback_block,
    )
    resp = llm.invoke(prompt)
    raw = getattr(resp, "content", str(resp))
    return _strip_code_block(raw)


def refactor_tool(name: str, instruction: str) -> str:
    """기존 generated 도구를 개선 지시에 따라 재생성하고 version 을 +1 한다.

    안전 정책(17) + 정적 검사 + sandbox(18) 를 모두 통과해야 갱신되며,
    실패하면 기존 도구를 그대로 유지한다.
    """
    from agent.engine.tool_registry import manager

    _reset_log()
    _log("Refactor 시작", f"{name}: {instruction}")

    entry = manager.find(name)
    if entry is None:
        return _failure_msg(f"도구 '{name}' 가 등록되어 있지 않습니다.")
    if entry.get("source") != "generated":
        return _failure_msg(
            f"'{name}' 은 builtin 도구라 refactor 대상이 아닙니다 "
            "(generated 도구만 개선할 수 있습니다)."
        )

    old_code = manager.read_code(name)
    if not old_code:
        return _failure_msg(f"'{name}' 의 소스 코드를 찾을 수 없습니다.")

    spec = {"name": name, "description": entry.get("description", ""), "inputs": []}

    feedback: Optional[str] = None
    code = ""
    last_reason = ""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            code = _refactor_coder(old_code, instruction, name, feedback)
            _log(f"Coder/refactor (시도 {attempt + 1})", code[:300])
        except Exception as e:  # noqa: BLE001
            return _failure_msg(f"Coder(refactor) 단계 실패: {e}")

        compliant, comp_reason = _compliance_agent(code, spec)
        _log(f"Compliance/refactor (시도 {attempt + 1})", comp_reason)
        if not compliant:
            last_reason = f"Compliance: {comp_reason}"
            feedback = f"이전 코드가 스캐폴드 규칙 위반: {comp_reason}\n정확히 따라 다시 작성하라."
            continue

        passed, test_reason = _test_agent(code, spec)
        _log(f"Test/refactor (시도 {attempt + 1})", test_reason)
        if not passed:
            last_reason = f"Test: {test_reason}"
            feedback = f"이전 코드가 검증 실패: {test_reason}\n이 문제를 해결해 다시 작성하라."
            continue

        sb_passed, sb_reason = _sandbox_agent(code, spec)
        _log(f"Sandbox/refactor (시도 {attempt + 1})", sb_reason)
        if sb_passed:
            break
        last_reason = f"Sandbox: {sb_reason}"
        feedback = f"이전 코드가 sandbox 실행 실패: {sb_reason}\n예외 없이 실행되게 고쳐라."
    else:
        return _failure_msg(
            f"refactor 재시도 {_MAX_RETRIES}회 초과 — 마지막 사유: {last_reason} "
            f"(기존 `{name}` 은 그대로 유지됨)"
        )

    new_version = manager.update(name, code=code)
    _log("Update", f"{name} → v{new_version}")
    return _refactor_success_msg(name, new_version, instruction)


# ──────────────────────────────────────────────────────────
# 출력 메시지
# ──────────────────────────────────────────────────────────


def _reuse_msg(entry: dict) -> str:
    return (
        f"♻️ 이미 동일한 기능의 도구 **`{entry['name']}`** (v{entry.get('version', 1)}) "
        f"가 등록되어 있어 재생성하지 않았습니다.\n\n"
        f"  - 설명: {entry.get('description', '')}\n\n"
        f"이 도구를 그대로 사용하시면 됩니다. 동작을 바꾸고 싶으면 개선 사항을 "
        f"알려주세요 (기존 도구를 개선합니다)."
    )


def _refactor_success_msg(name: str, version: Optional[int], instruction: str) -> str:
    return (
        f"🔧 도구 **`{name}`** 를 개선했습니다 (버전 v{version}).\n\n"
        f"  - 개선 지시: {instruction}\n"
        f"  - 검증: 안전 정책(17) + 정적 검사 + 컴파일 + sandbox 격리 실행(18) 통과\n\n"
        f"다음 turn 부터 개선된 도구가 사용됩니다."
    )


def _success_msg(spec: dict, registered_path: str) -> str:
    name = spec["name"]
    return (
        f"✅ 새 도구 **`{name}`** 가 자동 생성·등록되었습니다.\n\n"
        f"  - 설명: {spec.get('description', '')}\n"
        f"  - 등록 위치: `{registered_path}`\n"
        f"  - 검증: 안전 정책(17) + 정적 검사 + 컴파일 + sandbox 격리 실행(18) 통과\n\n"
        f"다음 turn 부터 LLM 이 이 도구를 직접 호출할 수 있습니다.\n\n"
        f"ℹ️ 위험 import·호출(`os`·`subprocess`·`eval` 등)은 안전 정책에서 차단되고, "
        f"코드는 메인 앱과 격리된 sandbox 에서 한 번 실행 검증을 거쳤습니다."
    )


def _failure_msg(reason: str) -> str:
    steps = [step for step, _ in _audit_log]
    return (
        f"❌ 새 도구 자동 생성에 실패했습니다.\n\n"
        f"  - 사유: {reason}\n"
        f"  - 실행된 단계: {' → '.join(steps)}\n\n"
        f"기존 도구로 처리 가능한 방향으로 요청을 다시 표현해 보세요."
    )
