"""
데이터 접근 단일 게이트웨이 — Phase 3 / 12단계

모든 도구(builtin / generated)는 `core.file_manager` 에 직접 접근하지 않고
이 모듈을 통해 파일을 읽고, 결과 파일을 등록한다.

이 모듈이 또한 에이전트 실행 중 공유 상태(파일 범위·결과 레지스트리)를 보유한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from core.file_manager import file_manager as _fm

# ──────────────────────────────────────────────────────────
# 공유 상태
# ──────────────────────────────────────────────────────────

# 작업 대상 파일 범위 (칩 태그 등으로 제한). None = 전체 허용
_file_scope: Optional[list[str]] = None

# 이번 에이전트 실행에서 도구가 생성한 결과 파일 경로
_created_files: list[Path] = []


# ──────────────────────────────────────────────────────────
# 파일 범위 (scope)
# ──────────────────────────────────────────────────────────


def set_file_scope(names: Optional[list[str]]) -> None:
    """작업 대상 파일을 제한한다. None / 빈 목록이면 전체 허용."""
    global _file_scope
    _file_scope = list(names) if names else None


def scope_active() -> bool:
    """현재 파일 범위가 설정돼 있는지."""
    return _file_scope is not None


def scoped(names: list[str]) -> list[str]:
    """파일명 목록을 현재 범위로 거른다 (범위 미설정 시 그대로)."""
    if _file_scope is None:
        return names
    return [n for n in names if n in _file_scope]


def is_in_scope(name: str) -> bool:
    return _file_scope is None or name in _file_scope


# ──────────────────────────────────────────────────────────
# 결과 파일 레지스트리
# ──────────────────────────────────────────────────────────


def reset_created_files() -> None:
    """에이전트 실행 직전 호출 — 이번 실행의 결과 파일 목록을 비운다."""
    _created_files.clear()


def get_created_files() -> list[Path]:
    """직전 에이전트 실행에서 생성된 결과 파일 경로 목록을 반환한다."""
    return list(_created_files)


def register_result(path) -> Path:
    """도구가 생성한 결과 파일을 레지스트리에 등록한다 (중복 제외)."""
    p = Path(path)
    if p not in _created_files:
        _created_files.append(p)
    return p


# ──────────────────────────────────────────────────────────
# 파일 접근 — file_manager 래핑
# ──────────────────────────────────────────────────────────


def list_files():
    """업로드된 파일 목록을 반환한다 (범위가 설정돼 있으면 필터)."""
    files = _fm.list_files()
    if _file_scope is not None:
        files = [f for f in files if f.name in _file_scope]
    return files


def read_file(name: str) -> Optional[pd.DataFrame]:
    """파일을 DataFrame 으로 읽는다 (퍼지 매칭 포함)."""
    return _fm.read_file(name)


def resolve_filename(name: str) -> Optional[str]:
    """근사 파일명을 실제 파일명으로 해석."""
    return _fm.resolve_filename(name)
