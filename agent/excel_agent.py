from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

from agent.llm_factory import get_llm
from core.excel_processor import excel_processor

_file_manager = None


def set_file_manager(fm) -> None:
    global _file_manager
    _file_manager = fm


# ──────────────────────────────────────────────────────────
# 결과 파일 레지스트리
#   - 에이전트 실행 중 도구가 생성한 결과 파일 경로를 모은다.
#   - 채팅 페이지가 실행 직후 get_created_files() 로 회수해
#     다운로드 버튼을 렌더링한다.
# ──────────────────────────────────────────────────────────

_created_files: list[Path] = []


def reset_created_files() -> None:
    """에이전트 실행 직전 호출 — 이번 실행의 결과 파일 목록을 비운다."""
    _created_files.clear()


def get_created_files() -> list[Path]:
    """직전 에이전트 실행에서 생성된 결과 파일 경로 목록을 반환한다."""
    return list(_created_files)


def _register(path) -> Path:
    """도구가 생성한 결과 파일을 레지스트리에 등록한다 (중복 제외)."""
    p = Path(path)
    if p not in _created_files:
        _created_files.append(p)
    return p


# ──────────────────────────────────────────────────────────
# Tools
# ──────────────────────────────────────────────────────────


@tool
def list_uploaded_files() -> str:
    """업로드된 모든 파일 목록과 메타데이터(행수·열수·크기)를 반환합니다."""
    files = _file_manager.list_files()
    if not files:
        return "업로드된 파일이 없습니다."
    lines = []
    for f in files:
        lines.append(
            f"- {f.name}: {f.rows}행 × {f.cols}열 / {f.size_kb:.1f}KB / "
            f"업로드: {f.uploaded_at[:10]}"
        )
    return "\n".join(lines)


@tool
def read_file_preview(filename: str) -> str:
    """파일의 컬럼 정보와 상위 5행 미리보기를 반환합니다."""
    df = _file_manager.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"
    return excel_processor.describe_df(df)


@tool
def merge_files_average(filenames: list[str]) -> str:
    """
    [거의 사용하지 않음 — 특수 케이스 전용]
    여러 파일을 '행 위치'(각 파일의 1번째 행끼리, 2번째 행끼리) 기준으로만
    평균 병합한다. 모든 파일의 행 순서와 개수가 완전히 동일할 때만 올바르다.
    '동일 항목'을 항목명으로 식별해 통합하려면 이 도구가 아니라
    merge_files_by_key 를 사용하라.
    결과는 results/ 폴더에 저장된다.
    """
    dfs: list[pd.DataFrame] = []
    missing: list[str] = []
    for name in filenames:
        df = _file_manager.read_file(name)
        if df is not None:
            dfs.append(df)
        else:
            missing.append(name)

    if not dfs:
        return "처리할 수 있는 파일이 없습니다."

    result_df = excel_processor.merge_average(dfs)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"merged_average_{ts}.xlsx"
    path = _register(excel_processor.save_excel(result_df, out_name))

    msg = [
        f"병합 완료: **{out_name}**",
        f"  - 결과: {len(result_df)}행 × {len(result_df.columns)}열",
        f"  - 처리 파일: {', '.join(filenames)}",
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다.",
    ]
    if missing:
        msg.append(f"  - 읽기 실패: {', '.join(missing)}")
    return "\n".join(msg)


@tool
def merge_files_concat(filenames: list[str]) -> str:
    """여러 파일을 단순히 행 방향으로 이어붙입니다. 결과는 results/ 폴더에 저장됩니다."""
    dfs: list[pd.DataFrame] = []
    for name in filenames:
        df = _file_manager.read_file(name)
        if df is not None:
            dfs.append(df)

    if not dfs:
        return "처리할 수 있는 파일이 없습니다."

    result_df = excel_processor.merge_concat(dfs)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"merged_concat_{ts}.xlsx"
    path = _register(excel_processor.save_excel(result_df, out_name))

    return (
        f"병합 완료: **{out_name}**\n"
        f"  - 결과: {len(result_df)}행 × {len(result_df.columns)}열\n"
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다."
    )


@tool
def merge_files_by_key(filenames: list[str], key_column: str = "") -> str:
    """
    [파일 통합의 기본 선택지]
    동일 양식의 여러 엑셀/CSV 파일을 '기준 컬럼(항목명)' 값이 같은 행끼리
    동일 항목으로 식별하여 통합한다. 행 순서나 항목 개수가 파일마다 달라도 안전하다.
    숫자 컬럼은 파일별 값의 평균, 텍스트 컬럼은 모두 같으면 유지·다르면 '값 상이',
    누락값은 'N/A'로 처리한다.
    결과는 3개 시트(통합결과·파일별비교·처리로그)를 가진 엑셀로 results/에 저장된다.
    key_column 미지정(빈 문자열) 시 항목명에 해당하는 컬럼을 자동 추정한다.

    "여러 파일을 하나로 통합", "동일 항목은 평균" 류의 요청은 거의 항상 이 도구를 쓴다.
    """
    named: list[tuple[str, pd.DataFrame]] = []
    missing: list[str] = []
    for name in filenames:
        df = _file_manager.read_file(name)
        if df is not None:
            named.append((name, df))
        else:
            missing.append(name)

    if not named:
        return "처리할 수 있는 파일이 없습니다."

    sheets = excel_processor.merge_by_key(named, key_col=key_column or None)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"통합결과_{ts}.xlsx"
    path = _register(excel_processor.save_multi_sheet(sheets, out_name))

    result_df = sheets["통합결과"]
    log_df = sheets["처리로그"]
    mismatch = int((log_df["구분"] == "불일치").sum()) if not log_df.empty else 0
    miss = int((log_df["구분"] == "누락").sum()) if not log_df.empty else 0

    # 실제 사용된 기준 컬럼을 처리로그에서 추출
    key_used = ""
    if not log_df.empty:
        kc = log_df[log_df["구분"] == "기준 컬럼"]
        if not kc.empty:
            key_used = str(kc.iloc[0]["항목"])

    msg = [
        f"통합 완료: **{out_name}**",
        f"  - 기준 컬럼: {key_used}" + (" (자동 추정)" if not key_column else ""),
        f"  - 통합 항목: {len(result_df)}개 × {len(result_df.columns)}열",
        f"  - 불일치(값 상이): {mismatch}건 / 누락 항목: {miss}건",
        "  - 시트 구성: 통합결과 · 파일별비교 · 처리로그",
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다.",
    ]
    if missing:
        msg.append(f"  - 읽기 실패: {', '.join(missing)}")
    return "\n".join(msg)


@tool
def filter_file(filename: str, column: str, operator: str, value: str) -> str:
    """
    파일에서 특정 조건으로 행을 필터링합니다.
    operator: '==', '!=', '>', '>=', '<', '<='
    결과는 results/ 폴더에 저장됩니다.
    """
    df = _file_manager.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"
    if column not in df.columns:
        return f"컬럼 '{column}'이 존재하지 않습니다. 사용 가능: {', '.join(df.columns)}"

    try:
        # Try numeric comparison first
        try:
            num_val = float(value)
            ops = {
                "==": df[df[column] == num_val],
                "!=": df[df[column] != num_val],
                ">": df[df[column] > num_val],
                ">=": df[df[column] >= num_val],
                "<": df[df[column] < num_val],
                "<=": df[df[column] <= num_val],
            }
        except ValueError:
            ops = {
                "==": df[df[column] == value],
                "!=": df[df[column] != value],
            }

        result_df = ops.get(operator)
        if result_df is None:
            return f"지원하지 않는 연산자: {operator}"
    except Exception as e:
        return f"필터 오류: {e}"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"filtered_{ts}.xlsx"
    path = _register(excel_processor.save_excel(result_df, out_name))
    return (
        f"필터 결과: **{out_name}**\n"
        f"  - 조건: {column} {operator} {value}\n"
        f"  - 결과: {len(result_df)}행\n"
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다."
    )


@tool
def aggregate_file(filename: str, group_by: str, agg_columns: list[str], method: str = "mean") -> str:
    """
    파일을 특정 컬럼으로 그룹화하고 집계합니다.
    method: 'mean', 'sum', 'count', 'min', 'max'
    결과는 results/ 폴더에 저장됩니다.
    """
    df = _file_manager.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"

    valid_methods = {"mean", "sum", "count", "min", "max"}
    if method not in valid_methods:
        return f"지원하지 않는 집계 방법: {method}. 사용 가능: {', '.join(valid_methods)}"

    try:
        cols_to_use = [c for c in agg_columns if c in df.columns]
        if not cols_to_use:
            cols_to_use = df.select_dtypes(include="number").columns.tolist()

        result_df = df.groupby(group_by)[cols_to_use].agg(method).reset_index()
    except Exception as e:
        return f"집계 오류: {e}"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"aggregated_{ts}.xlsx"
    path = _register(excel_processor.save_excel(result_df, out_name))
    return (
        f"집계 완료: **{out_name}**\n"
        f"  - 그룹: {group_by} / 집계: {method}\n"
        f"  - 결과: {len(result_df)}행\n"
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다."
    )


@tool
def get_statistics(filename: str) -> str:
    """파일의 수치형 컬럼에 대한 통계 요약(평균, 표준편차, 최솟값, 최댓값 등)을 반환합니다."""
    df = _file_manager.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"
    return df.describe(include="all").to_string()


ALL_TOOLS = [
    list_uploaded_files,
    read_file_preview,
    merge_files_average,
    merge_files_concat,
    merge_files_by_key,
    filter_file,
    aggregate_file,
    get_statistics,
]

# ──────────────────────────────────────────────────────────
# Agent builder
# ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """당신은 엑셀/CSV 파일 처리와 일반 대화를 함께 지원하는 한국어 AI 어시스턴트입니다.
사용자는 하나의 채팅창에서 모든 작업을 자연어로 요청합니다.

[일반 질문]
파일과 무관한 질문에는 도구를 호출하지 말고 바로 답변하세요.

[파일 작업 요청]
파일 통합·분석·필터링 등 데이터 작업 요청에는 아래 도구를 사용하세요.

사용 가능한 도구:
- list_uploaded_files: 업로드된 파일 목록 조회
- read_file_preview: 파일 내용 미리보기
- merge_files_by_key: 동일 양식 파일을 기준 컬럼(항목명)으로 통합 — 파일 통합의 기본 선택
  (숫자=평균, 텍스트=동일 유지/상이 표시, 3개 시트 엑셀 생성)
- merge_files_average: [거의 안 씀] 행 위치로만 평균 병합 (행 순서·개수가 완전히 같을 때만)
- merge_files_concat: 여러 파일을 단순 이어붙이기
- filter_file: 조건으로 행 필터링
- aggregate_file: 그룹별 집계
- get_statistics: 통계 요약

도구 선택 가이드:
- "여러 파일을 하나로 통합/합치기", "동일 항목/항목명 기준 통합", "같은 표 양식을
  하나로", "동일 항목은 평균값으로" → 반드시 merge_files_by_key 를 사용하세요.
- merge_files_average 는 행 위치 기반이라 거의 정답이 아닙니다. "평균"이라는
  단어만 보고 merge_files_average 를 고르지 마세요. 항목 식별이 필요하면
  merge_files_by_key 입니다.
- "N개 파일" 처럼 개수만 말하면 list_uploaded_files 로 업로드된 전체 파일을 대상으로 하세요.

기준 컬럼(key_column) 처리:
- 사용자가 기준 컬럼을 명시하지 않으면 key_column 을 빈 문자열("")로 두세요.
  도구가 항목명 컬럼을 자동 추정합니다.
- 사용자가 특정 컬럼명을 말하면 그 값을 key_column 에 전달하세요.
- 파일에 적절한 항목명 컬럼이 없어 보이거나 사용자 의도가 모호하면, 도구를
  바로 실행하지 말고 어떤 컬럼을 기준으로 할지 사용자에게 되물으세요.

처리 순서:
1. 먼저 list_uploaded_files 로 파일 목록을 확인하고
2. 필요하면 read_file_preview 로 구조를 파악한 뒤
3. 사용자 요청에 맞는 도구를 실행하세요.

[결과 제시 방식]
- 표나 목록에는 최종 결과값(금액·숫자)만 제시하세요.
- 셀 안에 "(파일명, N행) + ... = 합계" 같은 계산 과정이나 참조 컬럼·행 출처를
  절대 넣지 마세요. 오직 최종 값만 표시합니다.
- 계산 근거나 파일별 원본값이 필요하면, 생성된 엑셀의 '파일별비교' 시트를
  확인하라고 한 줄로 안내만 하세요.
- 결과 파일을 하이퍼링크나 "파일 보기", "결과 보기" 같은 링크로 만들지 마세요.
  파일 경로도 답변에 쓰지 마세요. 다운로드 버튼은 답변 아래에 앱이 자동으로 표시합니다.
- 답변 본문도 간결하게, 핵심 결과 위주로 한국어로 작성하세요."""


def build_excel_agent(
    provider: str, model: str, temperature: float = 0.0
) -> AgentExecutor:
    llm = get_llm(provider, model, temperature=temperature)

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])

    agent = create_tool_calling_agent(llm, ALL_TOOLS, prompt)
    return AgentExecutor(
        agent=agent,
        tools=ALL_TOOLS,
        verbose=False,
        max_iterations=10,
        handle_parsing_errors=True,
    )
