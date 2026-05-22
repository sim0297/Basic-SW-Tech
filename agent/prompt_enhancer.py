"""
프롬프트 자동 보강 (Prompt Enhancer) — Phase 2

9단계: 작업 의도 분류 (classify_intent)
  사용자 입력을 작업 유형으로 분류해 어떤 보강을 적용할지 결정한다.
10단계: 프롬프트 보강 엔진 (enhance)
  작업 유형 템플릿으로 persona·역할·절차·출력형식·제약을 갖춘 보강 지시를 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from config.settings import settings


class TaskIntent(str, Enum):
    """작업 유형."""

    MERGE = "파일 통합"
    ANALYZE = "데이터 분석"
    FILTER_AGGREGATE = "필터·집계"
    STATISTICS = "통계 요약"
    GENERAL = "일반 대화"
    UNKNOWN = "미분류"


# 작업 유형별 키워드 (규칙 기반 분류)
_INTENT_KEYWORDS: dict[TaskIntent, tuple[str, ...]] = {
    TaskIntent.MERGE: (
        "통합", "합치", "합쳐", "하나로", "병합", "merge", "묶어",
    ),
    TaskIntent.FILTER_AGGREGATE: (
        "필터", "추려", "추출", "조건", "그룹", "집계", "별로", "합계",
        "그룹화", "이상인", "이하인", "초과", "미만",
    ),
    TaskIntent.STATISTICS: (
        "통계", "요약", "describe", "표준편차", "분포", "최댓값", "최솟값",
        "평균값", "중앙값",
    ),
    TaskIntent.ANALYZE: (
        "분석", "비교", "얼마", "차이", "살펴", "몇 ", "몇개", "몇 개",
    ),
    TaskIntent.GENERAL: (
        "안녕", "고마", "누구", "뭐 할 수 있", "무엇을 할 수 있", "도움말",
        "사용법", "어떻게 써",
    ),
}


@dataclass
class IntentResult:
    """의도 분류 결과."""

    intent: TaskIntent
    method: str = "rule"          # "rule" | "llm" | "heuristic"
    matched: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.intent.value} (via {self.method})"


def _rule_hits(text: str) -> dict[TaskIntent, list[str]]:
    """각 작업 유형별로 매칭된 키워드 목록을 반환한다."""
    low = text.lower()
    hits: dict[TaskIntent, list[str]] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        matched = [kw for kw in keywords if kw.lower() in low]
        if matched:
            hits[intent] = matched
    return hits


_LLM_CLASSIFY_PROMPT = """다음 사용자 입력을 아래 6개 작업 유형 중 하나로 분류하라.

유형(정확히 이 라벨 중 하나만 출력):
- 파일 통합
- 데이터 분석
- 필터·집계
- 통계 요약
- 일반 대화
- 미분류

업로드된 파일: {has_files}
사용자 입력: {prompt}

설명 없이 유형 라벨만 한 줄로 출력하라."""


def _classify_with_llm(prompt: str, has_files: bool, llm=None) -> TaskIntent | None:
    """경량 LLM 호출로 의도를 분류한다. 실패 시 None."""
    try:
        if llm is None:
            from agent.llm_factory import get_llm

            llm = get_llm(
                settings.DEFAULT_PROVIDER, settings.DEFAULT_MODEL, temperature=0.0
            )
        message = _LLM_CLASSIFY_PROMPT.format(
            has_files="있음" if has_files else "없음", prompt=prompt
        )
        resp = llm.invoke(message)
        text = (getattr(resp, "content", "") or "").strip()
        for intent in TaskIntent:
            if intent.value in text:
                return intent
    except Exception:
        return None
    return None


def classify_intent(
    user_prompt: str,
    has_files: bool = False,
    use_llm_fallback: bool = True,
    llm=None,
) -> IntentResult:
    """
    사용자 입력의 작업 유형을 분류한다.

    Args:
        user_prompt: 사용자 원문 프롬프트
        has_files: 업로드된 파일 존재 여부 (컨텍스트)
        use_llm_fallback: 규칙이 모호할 때 LLM 호출 허용 여부
        llm: 분류용 LLM (None이면 기본 모델 사용)

    Returns:
        IntentResult — intent / method / matched
    """
    prompt = (user_prompt or "").strip()
    if not prompt:
        return IntentResult(TaskIntent.GENERAL, "heuristic")

    hits = _rule_hits(prompt)
    data_hits = {k: v for k, v in hits.items() if k != TaskIntent.GENERAL}

    # 1) 규칙 기반 — 데이터 작업 키워드가 일반 대화보다 우선
    if data_hits:
        ranked = sorted(data_hits.items(), key=lambda kv: len(kv[1]), reverse=True)
        # 단독 최고이거나 2위와 점수 차이가 있으면 채택
        if len(ranked) == 1 or len(ranked[0][1]) > len(ranked[1][1]):
            return IntentResult(ranked[0][0], "rule", ranked[0][1])
        # 동점 → LLM 폴백으로 위임
    elif TaskIntent.GENERAL in hits:
        return IntentResult(TaskIntent.GENERAL, "rule", hits[TaskIntent.GENERAL])

    # 2) LLM 폴백
    if use_llm_fallback:
        llm_intent = _classify_with_llm(prompt, has_files, llm)
        if llm_intent is not None:
            return IntentResult(llm_intent, "llm")

    # 3) 휴리스틱 폴백
    if data_hits:
        best = max(data_hits.items(), key=lambda kv: len(kv[1]))
        return IntentResult(best[0], "heuristic", best[1])
    return IntentResult(
        TaskIntent.ANALYZE if has_files else TaskIntent.GENERAL, "heuristic"
    )


# ──────────────────────────────────────────────────────────
# 10단계 — 프롬프트 보강 엔진
# ──────────────────────────────────────────────────────────

_POLISH_PROMPT = """다음은 AI 에이전트에게 줄 작업 지시문이다.
의미·항목을 그대로 유지하면서 자연스럽고 명확한 한국어로 다듬어라.
새로운 내용을 추가하거나 항목을 빼지 마라. 다듬은 지시문만 출력하라.

---
{instruction}
---"""


def _render_context(context: dict | None) -> list[str]:
    """컨텍스트(파일 정보 등)를 보강 지시용 라인으로 변환한다."""
    if not context:
        return []
    lines: list[str] = []
    file_names = context.get("file_names")
    if file_names:
        lines.append(f"- 업로드된 파일: {', '.join(file_names)}")
    note = context.get("note")
    if note:
        lines.append(f"- {note}")
    return lines


def enhance(
    user_prompt: str,
    intent: TaskIntent,
    context: dict | None = None,
    polish: bool = False,
    llm=None,
) -> str:
    """
    작업 유형 템플릿으로 보강된 시스템 지시문을 생성한다.

    Args:
        user_prompt: 사용자 원문 프롬프트
        intent: classify_intent 로 판별된 작업 유형
        context: 참고 컨텍스트 (예: {"file_names": [...]})
        polish: True 면 LLM으로 지시문을 자연스럽게 다듬음
        llm: polish 용 LLM (None이면 기본 모델)

    Returns:
        보강된 시스템 지시문 텍스트
    """
    from config.prompt_templates import get_template

    tpl = get_template(intent)

    lines: list[str] = [
        "## 역할",
        f"당신은 {tpl.persona}입니다. {tpl.role}.",
    ]
    if tpl.procedure:
        lines += ["", "## 처리 절차"]
        lines += [f"{i}. {step}" for i, step in enumerate(tpl.procedure, 1)]
    if tpl.output_format:
        lines += ["", "## 출력 형식", tpl.output_format]
    if tpl.constraints:
        lines += ["", "## 제약사항"]
        lines += [f"- {c}" for c in tpl.constraints]

    ctx_lines = _render_context(context)
    if ctx_lines:
        lines += ["", "## 참고 컨텍스트"] + ctx_lines

    prompt = (user_prompt or "").strip()
    if prompt:
        lines += ["", "## 사용자 요청", prompt]

    instruction = "\n".join(lines)

    if polish:
        polished = _polish_with_llm(instruction, llm)
        if polished:
            return polished
    return instruction


def _polish_with_llm(instruction: str, llm=None) -> str | None:
    """LLM으로 보강 지시문을 자연스럽게 다듬는다. 실패 시 None."""
    try:
        if llm is None:
            from agent.llm_factory import get_llm

            llm = get_llm(
                settings.DEFAULT_PROVIDER, settings.DEFAULT_MODEL, temperature=0.2
            )
        resp = llm.invoke(_POLISH_PROMPT.format(instruction=instruction))
        text = (getattr(resp, "content", "") or "").strip()
        return text or None
    except Exception:
        return None
