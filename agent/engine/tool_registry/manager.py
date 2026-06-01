"""
Tool Registry 생애주기 관리자 — Phase 3 / 19단계

`registry.json` + `tools/generated/*.py` 의 **영속 변경 단일 게이트웨이**.

- `registry.py`  : 읽기 전용 동적 로더 (`get_tools` / `execute`) — 12단계
- `manager.py`   : 영속 변경 CRUD (register / update / delete / promote) — 19단계

registry.json 은 이 프로젝트에서 **도구 메타데이터 리스트**(dict 가 아님) 다.
각 항목: `{name, description, module, function, source, version, created_at?, updated_at?}`

⚠️ 같은 프로세스(streamlit) 안에서 도구를 추가/갱신하면 Python 이 이미 import 한
모듈을 캐시해 옛 코드를 돌려준다. 그래서 모든 쓰기 후 `_invalidate()` 로
`sys.modules` 캐시를 비워, 다음 `registry.get_tools()` 가 새 코드를 로드하게 한다.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REGISTRY_FILE = _HERE / "registry.json"
_TOOLS_DIR = _HERE.parent / "tools"
_GENERATED_DIR = _TOOLS_DIR / "generated"
_BUILTIN_DIR = _TOOLS_DIR / "builtin"

_GENERATED_PKG = "agent.engine.tools.generated"
_BUILTIN_PKG = "agent.engine.tools.builtin"


# ──────────────────────────────────────────────────────────
# 내부 입출력
# ──────────────────────────────────────────────────────────


def _load() -> list[dict]:
    if not _REGISTRY_FILE.exists():
        return []
    try:
        data = json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:  # noqa: BLE001
        print(f"[manager] registry.json 읽기 실패: {e}")
        return []


def _save(tools: list[dict]) -> None:
    _REGISTRY_FILE.write_text(
        json.dumps(tools, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _invalidate(*module_paths: str) -> None:
    """import 캐시를 비워 다음 import 가 갱신된 코드를 로드하게 한다."""
    importlib.invalidate_caches()
    for m in module_paths:
        sys.modules.pop(m, None)


def _module_path(name: str, source: str) -> str:
    pkg = _GENERATED_PKG if source == "generated" else _BUILTIN_PKG
    return f"{pkg}.{name}"


# ──────────────────────────────────────────────────────────
# 조회 (Read)
# ──────────────────────────────────────────────────────────


def get_all() -> list[dict]:
    """등록된 모든 도구 메타데이터."""
    return _load()


def find(name: str) -> Optional[dict]:
    """이름으로 단일 도구 메타데이터 조회. 없으면 None."""
    for t in _load():
        if t.get("name") == name:
            return t
    return None


def exists(name: str) -> bool:
    return find(name) is not None


def list_generated() -> list[dict]:
    """generated(자동 생성) 도구만 반환."""
    return [t for t in _load() if t.get("source") == "generated"]


def search(query: str, source: Optional[str] = None) -> list[dict]:
    """name / description 에 query 단어가 포함된 도구 검색 (점수 내림차순)."""
    q = (query or "").lower().strip()
    if not q:
        return []
    words = [w for w in q.split() if len(w) >= 2]
    scored: list[tuple[int, dict]] = []
    for t in _load():
        if source and t.get("source") != source:
            continue
        haystack = (t.get("name", "") + " " + t.get("description", "")).lower()
        score = sum(1 for w in words if w in haystack)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored]


# ──────────────────────────────────────────────────────────
# 생성 (Create) — generated 도구 등록 (이미 있으면 교체, version 유지)
# ──────────────────────────────────────────────────────────


def register(
    name: str,
    description: str,
    code: str,
    *,
    source: str = "generated",
    function: Optional[str] = None,
    version: int = 1,
) -> str:
    """새 도구를 등록한다 (.py 저장 + registry.json 갱신). 등록 경로를 반환.

    같은 이름이 이미 있으면 코드·메타를 교체한다 (creation_pipeline 재시도 대응).
    """
    function = function or name
    module = _module_path(name, source)

    if source == "generated":
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
        path = _GENERATED_DIR / f"{name}.py"
        path.write_text(code, encoding="utf-8")
    else:
        path = _BUILTIN_DIR / f"{name}.py"

    entry = {
        "name": name,
        "description": description,
        "module": module,
        "function": function,
        "source": source,
        "version": version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    tools = [t for t in _load() if t.get("name") != name]
    tools.append(entry)
    _save(tools)
    _invalidate(module)
    return str(path)


# ──────────────────────────────────────────────────────────
# 갱신 (Update) — version 자동 +1 (refactor_tool 이 사용)
# ──────────────────────────────────────────────────────────


def update(name: str, code: Optional[str] = None, **fields) -> Optional[int]:
    """기존 도구 메타/코드 갱신. version 자동 +1. 새 version 반환, 없으면 None."""
    tools = _load()
    for t in tools:
        if t.get("name") != name:
            continue

        if code is not None and t.get("source") == "generated":
            (_GENERATED_DIR / f"{name}.py").write_text(code, encoding="utf-8")

        t.update(fields)
        t["version"] = int(t.get("version", 1)) + 1
        t["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save(tools)
        _invalidate(t.get("module", _module_path(name, t.get("source", "generated"))))
        return t["version"]
    return None


# ──────────────────────────────────────────────────────────
# 삭제 (Delete)
# ──────────────────────────────────────────────────────────


def delete(name: str, remove_file: bool = True) -> bool:
    """도구 등록 해제. generated 면 .py 파일도 삭제. 대상 없으면 False."""
    tools = _load()
    entry = next((t for t in tools if t.get("name") == name), None)
    if entry is None:
        return False

    _save([t for t in tools if t.get("name") != name])

    if remove_file and entry.get("source") == "generated":
        f = _GENERATED_DIR / f"{name}.py"
        if f.exists():
            f.unlink()
    _invalidate(entry.get("module", ""))
    return True


# ──────────────────────────────────────────────────────────
# 승격 (Promote) — 검증된 generated 도구를 정식 builtin 으로
# ──────────────────────────────────────────────────────────


def promote(name: str) -> bool:
    """generated 도구를 builtin 으로 승격한다 (파일 이동 + 메타 갱신).

    사용자 검토 후 정식 도구로 올리는 경로. 대상이 없거나 generated 가 아니면 False.
    """
    tools = _load()
    entry = next((t for t in tools if t.get("name") == name), None)
    if entry is None or entry.get("source") != "generated":
        return False

    src = _GENERATED_DIR / f"{name}.py"
    if not src.exists():
        return False

    _BUILTIN_DIR.mkdir(parents=True, exist_ok=True)
    dst = _BUILTIN_DIR / f"{name}.py"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    src.unlink()

    old_module = entry.get("module", _module_path(name, "generated"))
    entry["source"] = "builtin"
    entry["module"] = _module_path(name, "builtin")
    entry["promoted_at"] = datetime.now().isoformat(timespec="seconds")
    _save(tools)
    _invalidate(old_module, entry["module"])
    return True


# ──────────────────────────────────────────────────────────
# 코드 조회 — refactor 등에서 기존 소스를 읽을 때
# ──────────────────────────────────────────────────────────


def read_code(name: str) -> Optional[str]:
    """등록된 도구의 소스 코드를 반환한다 (없으면 None)."""
    entry = find(name)
    if entry is None:
        return None
    base = _GENERATED_DIR if entry.get("source") == "generated" else _BUILTIN_DIR
    f = base / f"{name}.py"
    return f.read_text(encoding="utf-8") if f.exists() else None
