"""
Tool Registry — Phase 3 / 12단계

`registry.json` 의 메타데이터를 읽어 도구 모듈을 동적 import 하고,
LangChain `@tool` 객체 리스트를 노출한다 (읽기 전용).

CRUD/refactor 는 19단계에서 manager.py 에 분리될 예정.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Optional

_REGISTRY_FILE = Path(__file__).parent / "registry.json"


def _load_metadata() -> list[dict]:
    """registry.json 의 도구 메타데이터를 로드한다."""
    if not _REGISTRY_FILE.exists():
        return []
    return json.loads(_REGISTRY_FILE.read_text(encoding="utf-8"))


def list_specs() -> list[dict]:
    """등록된 도구 메타데이터 그대로 반환."""
    return _load_metadata()


def get_tools() -> list:
    """등록된 모든 도구의 LangChain @tool 객체 리스트를 반환."""
    tools = []
    for meta in _load_metadata():
        try:
            module = importlib.import_module(meta["module"])
            fn = getattr(module, meta["function"])
            tools.append(fn)
        except Exception as e:
            # 로드 실패한 도구는 건너뜀 (감사 로그용)
            print(f"[registry] 도구 로드 실패 {meta.get('name')}: {e}")
    return tools


def get_tool(name: str):
    """이름으로 도구 @tool 객체 조회. 없으면 None."""
    for meta in _load_metadata():
        if meta["name"] == name:
            module = importlib.import_module(meta["module"])
            return getattr(module, meta["function"])
    return None


def get_spec(name: str) -> Optional[dict]:
    """이름으로 메타데이터 조회."""
    for meta in _load_metadata():
        if meta["name"] == name:
            return meta
    return None


def execute(name: str, args: dict) -> Any:
    """이름으로 도구를 실행한다 (orchestrator 가 사용)."""
    tool = get_tool(name)
    if tool is None:
        raise KeyError(f"Tool '{name}' not registered")
    return tool.invoke(args)
