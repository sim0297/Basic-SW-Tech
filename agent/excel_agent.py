"""
excel_agent.py — Phase 3 / 12단계 이후의 얇은 호환 레이어.

도구는 `agent/engine/tools/builtin/` 으로 분리되어 있고,
`agent/engine/tool_registry/registry.json` + `registry.py` 로 동적 로드된다.

이 모듈은 기존 `pages/chat.py` 의 임포트(`build_excel_agent`, `ALL_TOOLS`,
`set_file_scope`, `reset_created_files`, `get_created_files`, `set_file_manager`)
를 유지하기 위한 호환 진입점이다. Phase 3 / 13단계에서 `orchestrator.py` 가
도입되면 `pages/chat.py` 가 그쪽으로 이전된다.
"""
from __future__ import annotations

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from agent.engine.data import loader
from agent.engine.tool_registry import registry
from agent.llm_factory import get_llm

# ──────────────────────────────────────────────────────────
# 도구 — registry 에서 동적 로드 (현재 9개 builtin)
# ──────────────────────────────────────────────────────────

ALL_TOOLS = registry.get_tools()

# ──────────────────────────────────────────────────────────
# 상태 함수 — loader 로 위임
# ──────────────────────────────────────────────────────────

set_file_scope = loader.set_file_scope
reset_created_files = loader.reset_created_files
get_created_files = loader.get_created_files


def set_file_manager(fm) -> None:
    """
    호환용 no-op.
    구조 재편 후 loader 가 core.file_manager.file_manager 를 직접 사용한다.
    """
    return None


# ──────────────────────────────────────────────────────────
# 시스템 프롬프트 (13단계에서 orchestrator 로 이동 예정)
# ──────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """당신은 엑셀/CSV 파일 처리와 일반 대화를 함께 지원하는 한국어 AI 어시스턴트입니다.
사용자는 하나의 채팅창에서 모든 작업을 자연어로 요청합니다.

[일반 질문]
파일과 무관한 질문에는 도구를 호출하지 말고 바로 답변하세요.

[파일 작업 요청]
파일 통합·분석·필터링 등 데이터 작업 요청에는 아래 도구를 사용하세요.

사용 가능한 도구:
- list_uploaded_files: 업로드된 파일 목록 조회
- read_file_preview: 파일 내용 미리보기 (상위 일부 행만 보임)
- lookup_rows: 특정 항목·값을 가진 행을 찾아 그 행의 모든 값을 조회
- merge_files_by_key: 동일 양식 파일을 기준 컬럼(항목명)으로 통합 — 파일 통합의 기본 선택
  (숫자=평균, 텍스트=동일 유지/상이 표시, 3개 시트 엑셀 생성)
- merge_files_average: [거의 안 씀] 행 위치로만 평균 병합 (행 순서·개수가 완전히 같을 때만)
- merge_files_concat: 여러 파일을 단순 이어붙이기
- filter_file: 조건으로 행 필터링
- aggregate_file: 그룹별 집계
- get_statistics: 통계 요약

특정 항목 조회 주의:
- "X 항목의 값은?" 같은 질문은 read_file_preview 만으로 판단하지 마세요.
  미리보기는 일부 행만 보이므로, 반드시 lookup_rows 로 해당 항목을 검색한 뒤
  답하세요. 미리보기에 없다고 "데이터가 없다"고 단정하지 마세요.

도구 선택 가이드:
- "여러 파일을 하나로 통합/합치기", "동일 항목/항목명 기준 통합", "같은 표 양식을
  하나로", "동일 항목은 평균값으로" → 반드시 merge_files_by_key 를 사용하세요.
- merge_files_average 는 행 위치 기반이라 거의 정답이 아닙니다. "평균"이라는
  단어만 보고 merge_files_average 를 고르지 마세요. 항목 식별이 필요하면
  merge_files_by_key 입니다.
- "N개 파일" 처럼 개수만 말하면 list_uploaded_files 로 업로드된 전체 파일을 대상으로 하세요.
- "X의 값/합계는 얼마?" 같은 단순 조회·계산 질문에는 lookup_rows 로 실제 값을
  읽어 숫자로 답하세요. aggregate_file·filter_file·merge 등 파일 생성 도구는
  사용자가 "표/파일로 만들어 달라"고 명시할 때만 사용하세요.

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
- '파일별비교' 시트 안내는 merge_files_by_key 로 통합 엑셀을 **실제로 생성한
  경우에만** 하세요. 단순 조회·계산 질문(예: "X 항목 금액 얼마야?")에는
  결과값만 간결히 답하고, 파일·시트 안내를 덧붙이지 마세요.
- 이번 응답에서 결과 파일을 만들지 않았다면, 파일·다운로드·시트와 관련된
  어떤 안내도 하지 마세요.
- 결과 파일을 하이퍼링크나 "파일 보기", "결과 보기" 같은 링크로 만들지 마세요.
  파일 경로도 답변에 쓰지 마세요. 다운로드 버튼은 답변 아래에 앱이 자동으로 표시합니다.
- 답변 본문도 간결하게, 핵심 결과 위주로 한국어로 작성하세요.

[답변 정합성 — 매우 중요]
- 답변에 쓰는 모든 수치는 도구 실행 결과에서 직접 가져오세요. 도구가 반환하지
  않은 값을 추정하거나 지어내지 마세요.
- 도구를 실행하지 않고 머릿속 계산만으로 수치를 답하지 마세요. 반드시 도구로
  실제 값을 확인한 뒤 답하세요.
- 결과 파일을 생성했다면, 답변 내용(수치·항목)은 반드시 그 파일의 내용과
  일치해야 합니다. 파일과 다른 별도 계산값을 답하지 마세요."""


# ──────────────────────────────────────────────────────────
# Agent builder (호환 — 13단계에서 orchestrator 로 대체 예정)
# ──────────────────────────────────────────────────────────


def build_excel_agent(
    provider: str, model: str, temperature: float = 0.0
) -> AgentExecutor:
    llm = get_llm(provider, model, temperature=temperature)

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("system", "{task_guidance}"),
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
