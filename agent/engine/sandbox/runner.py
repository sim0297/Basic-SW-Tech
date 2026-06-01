"""
Sandbox 실행 — Phase 3 / 18단계

안전 정책(17단계)을 통과한 generated 도구 코드를 **메인 앱과 격리된 별도
subprocess** 에서 실제로 한 번 실행해 본다. 핵심 원칙:

- **격리**: 본체 인터프리터가 아니라 `subprocess` 로 실행 → 도구가 무엇을 하든
  본체 프로세스(Streamlit·에이전트)는 영향받지 않는다.
- **모의 loader 주입**: 실제 업로드 파일에 접근하지 못하도록
  `agent.engine.data.loader` / `agent.engine.tools._helpers` 를 합성 샘플
  데이터로 대체해 `sys.modules` 에 주입한다 (참조 구조의 sample_data 패턴).
- **자원 상한**: timeout(기본 30초) + 메모리 상한(posix, best-effort).
- **결과만 회수**: stdout/stderr/예외/반환값을 캡처해 `SandboxResult` 로만 반환.

`creation_pipeline` 이 Test(정적 검사) 통과 후 Register 전에 이 모듈로 도구를
한 번 실행시켜, 런타임에 터지거나 폭주하는 코드를 등록 전에 걸러낸다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PYTHON = sys.executable
TIMEOUT_SECONDS = 30
MEMORY_LIMIT_MB = 2048          # posix best-effort. pandas/numpy import 여유 고려.
MAX_OUTPUT_SIZE = 10_000

# 합성 샘플 대신 실제 업로드 파일 하나를 샘플로 쓰고 싶을 때 configure() 로 설정.
_SAMPLE_FILE: Optional[Path] = None


@dataclass
class SandboxResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    return_value: str = ""
    error: str = ""

    def summary(self) -> str:
        if self.timed_out:
            return f"sandbox {self.error or 'timeout 초과'}"
        if self.success:
            return f"sandbox 실행 성공 — 반환: {self.return_value[:120]}"
        return f"sandbox 실행 실패 — {self.error or self.stderr[:200]}"


def configure(sample_file: Optional[str]) -> None:
    """모의 loader 가 사용할 실제 샘플 파일 경로(선택)를 설정한다.

    설정하지 않으면 합성 샘플 DataFrame 을 사용한다.
    """
    global _SAMPLE_FILE
    _SAMPLE_FILE = Path(sample_file).resolve() if sample_file else None


# ──────────────────────────────────────────────────────────
# 모의 환경 코드 — sandbox subprocess 안에서 sys.modules 를 가로챈다
# ──────────────────────────────────────────────────────────


def _build_sandbox_env(sample_file: Optional[Path]) -> str:
    """`agent.engine.data.loader` 와 `..tools._helpers` 의 모의 구현 코드."""
    sample_literal = repr(str(sample_file)) if sample_file else "None"
    return f'''
"""sandbox 모의 환경. 실제 loader/helpers 대신 합성 샘플로 응답한다."""
import sys
import types
from pathlib import Path

import pandas as pd

_SAMPLE_FILE = {sample_literal}


# ── 합성 샘플 데이터 ────────────────────────────────────────
def _make_sample(seed: int) -> "pd.DataFrame":
    base = {{
        "항목": [f"항목{{seed}}_{{i}}" for i in range(1, 6)],
        "값1": [10 * seed + i for i in range(5)],
        "값2": [1.5 * (seed + i) for i in range(5)],
        "분류": ["A", "B", "A", "C", "B"],
    }}
    return pd.DataFrame(base)


_SAMPLE_NAMES = ["sample_a.xlsx", "sample_b.xlsx"]
_SAMPLES = {{name: _make_sample(i + 1) for i, name in enumerate(_SAMPLE_NAMES)}}

# 실제 샘플 파일이 지정되면 첫 샘플을 그것으로 대체
if _SAMPLE_FILE:
    try:
        p = Path(_SAMPLE_FILE)
        if p.suffix.lower() in (".xlsx", ".xls"):
            _SAMPLES[_SAMPLE_NAMES[0]] = pd.read_excel(p)
        else:
            _SAMPLES[_SAMPLE_NAMES[0]] = pd.read_csv(p)
    except Exception:
        pass


class _FileInfo:
    def __init__(self, name, df):
        self.name = name
        self.path = Path(name)
        self.rows = len(df)
        self.cols = df.shape[1]
        self.size = self.rows * self.cols * 8
    @property
    def size_kb(self):
        return self.size / 1024
    def to_dict(self):
        return {{"name": self.name, "rows": self.rows, "cols": self.cols}}


# ── 모의 loader ─────────────────────────────────────────────
_created = []

def list_files():
    return [_FileInfo(n, df) for n, df in _SAMPLES.items()]

def read_file(name):
    if name in _SAMPLES:
        return _SAMPLES[name].copy()
    # 퍼지 매칭 흉내
    for n, df in _SAMPLES.items():
        if name and (name in n or n in name):
            return df.copy()
    return None

def resolve_filename(name):
    if name in _SAMPLES:
        return name
    for n in _SAMPLES:
        if name and (name in n or n in name):
            return n
    return None

def register_result(path):
    p = Path(path)
    _created.append(p)
    return p

def reset_created_files():
    _created.clear()

def get_created_files():
    return list(_created)

def set_file_scope(names):
    pass

def scope_active():
    return False

def scoped(names):
    return names

def is_in_scope(name):
    return True


# ── 모의 _helpers (결과 저장을 temp cwd 로 격리) ──────────────
def save_xlsx_and_register(df, prefix="result"):
    out = Path(f"{{prefix}}_sandbox.xlsx")
    try:
        df.to_excel(out, index=False)
    except Exception:
        out.write_bytes(b"")
    return register_result(out)

def save_multi_sheet_and_register(sheets, prefix="통합결과"):
    out = Path(f"{{prefix}}_sandbox.xlsx")
    try:
        with pd.ExcelWriter(out) as w:
            for sname, sdf in sheets.items():
                sdf.to_excel(w, sheet_name=str(sname)[:31], index=False)
    except Exception:
        out.write_bytes(b"")
    return register_result(out)

def now_stamp():
    return "sandbox"


# ── sys.modules 주입 ────────────────────────────────────────
def _mod(name):
    m = types.ModuleType(name)
    return m

_loader = _mod("agent.engine.data.loader")
for _n in ("list_files", "read_file", "resolve_filename", "register_result",
           "reset_created_files", "get_created_files", "set_file_scope",
           "scope_active", "scoped", "is_in_scope"):
    setattr(_loader, _n, globals()[_n])

_helpers = _mod("agent.engine.tools._helpers")
for _n in ("save_xlsx_and_register", "save_multi_sheet_and_register", "now_stamp"):
    setattr(_helpers, _n, globals()[_n])

_agent = _mod("agent")
_agent_engine = _mod("agent.engine")
_agent_engine_data = _mod("agent.engine.data")
_agent_engine_data.loader = _loader
_agent_engine_tools = _mod("agent.engine.tools")
_agent_engine_tools._helpers = _helpers

sys.modules["agent"] = _agent
sys.modules["agent.engine"] = _agent_engine
sys.modules["agent.engine.data"] = _agent_engine_data
sys.modules["agent.engine.data.loader"] = _loader
sys.modules["agent.engine.tools"] = _agent_engine_tools
sys.modules["agent.engine.tools._helpers"] = _helpers

SAMPLE_NAMES = _SAMPLE_NAMES
'''


# ──────────────────────────────────────────────────────────
# 격리 실행 runner 템플릿
# ──────────────────────────────────────────────────────────

_RUNNER_TEMPLATE = '''
import json
import sys
import traceback

import _sandbox_env   # noqa: F401  (sys.modules 모의 주입)
import tool_module

TOOL_NAME = {tool_name!r}

with open("_call.json", encoding="utf-8") as f:
    kwargs = json.load(f)

fn = getattr(tool_module, TOOL_NAME, None)
if fn is None:
    print("__SANDBOX_ERR__ NameError")
    print(f"도구 함수 '{{TOOL_NAME}}' 가 모듈에 없음")
    sys.exit(2)

# @tool 데코레이터(StructuredTool)면 원본 함수(.func)를 직접 호출
target = getattr(fn, "func", None) or fn

try:
    rv = target(**kwargs)
    print("__SANDBOX_OK__")
    print(repr(rv)[:2000])
    sys.exit(0)
except Exception as e:
    traceback.print_exc()
    print(f"__SANDBOX_ERR__ {{type(e).__name__}}")
    print(str(e)[:500])
    sys.exit(2)
'''


def _limit_resources():
    """posix best-effort 메모리 상한. (subprocess preexec_fn)"""
    try:
        import resource

        nbytes = MEMORY_LIMIT_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
    except Exception:
        pass


# ──────────────────────────────────────────────────────────
# 인자 합성 — spec inputs 로부터 안전한 호출 인자 만들기
# ──────────────────────────────────────────────────────────


def _synthesize_args(spec: dict) -> dict:
    """spec inputs 타입/이름 힌트로 sandbox 호출용 더미 인자를 만든다."""
    args: dict = {}
    for inp in spec.get("inputs", []):
        name = (inp.get("name") or "arg").strip()
        typ = (inp.get("type") or "str").lower()
        lname = name.lower()
        is_list = typ.startswith("list") or "[" in typ
        if "file" in lname or "파일" in name:
            args[name] = ["sample_a.xlsx", "sample_b.xlsx"] if is_list else "sample_a.xlsx"
        elif any(k in lname for k in ("col", "key", "컬럼", "기준")) or "항목" in name:
            args[name] = ["항목"] if is_list else "항목"
        elif "int" in typ:
            args[name] = 1
        elif "float" in typ:
            args[name] = 1.0
        elif "bool" in typ:
            args[name] = True
        elif is_list:
            args[name] = ["sample_a.xlsx"]
        else:
            args[name] = "sample_a.xlsx" if not args else ""
    return args


# ──────────────────────────────────────────────────────────
# 실행 진입점
# ──────────────────────────────────────────────────────────


def run_in_sandbox(
    tool_code: str,
    tool_name: str,
    call_args: dict,
    timeout: int = TIMEOUT_SECONDS,
) -> SandboxResult:
    """도구 코드를 격리 subprocess 에서 주어진 인자로 한 번 실행한다."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "_sandbox_env.py").write_text(
            _build_sandbox_env(_SAMPLE_FILE), encoding="utf-8"
        )
        (tmp / "tool_module.py").write_text(tool_code, encoding="utf-8")
        (tmp / "_call.json").write_text(
            json.dumps(call_args, ensure_ascii=False), encoding="utf-8"
        )
        (tmp / "_runner.py").write_text(
            _RUNNER_TEMPLATE.format(tool_name=tool_name), encoding="utf-8"
        )

        preexec = _limit_resources if sys.platform != "win32" else None
        try:
            proc = subprocess.run(
                # -E -s: 부모의 PYTHONPATH / user site-packages 를 무시(환경 격리)하되
                # 임시 디렉토리는 sys.path 에 남겨 모의 모듈 import 가 되게 한다.
                [PYTHON, "-E", "-s", "_runner.py"],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
                preexec_fn=preexec,
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                timed_out=True,
                error=f"timeout ({timeout}s 초과) — 무한 루프/폭주 의심",
            )

        stdout = (proc.stdout or "")[:MAX_OUTPUT_SIZE]
        stderr = (proc.stderr or "")[:MAX_OUTPUT_SIZE]
        ok = proc.returncode == 0 and "__SANDBOX_OK__" in stdout

        return_value = ""
        error = ""
        if ok:
            after = stdout.split("__SANDBOX_OK__", 1)[1].strip()
            return_value = after[:1000]
        else:
            if "__SANDBOX_ERR__" in stdout:
                error = stdout.split("__SANDBOX_ERR__", 1)[1].strip()[:500]
            else:
                error = stderr[-500:] or f"비정상 종료 (returncode={proc.returncode})"

        return SandboxResult(
            success=ok,
            stdout=stdout,
            stderr=stderr,
            return_value=return_value,
            error=error,
        )


def smoke_test(
    tool_code: str,
    spec: dict,
    timeout: int = TIMEOUT_SECONDS,
) -> SandboxResult:
    """spec 으로부터 인자를 합성해 sandbox 에서 도구를 한 번 실행(smoke)한다."""
    args = _synthesize_args(spec)
    return run_in_sandbox(tool_code, spec["name"], args, timeout=timeout)
