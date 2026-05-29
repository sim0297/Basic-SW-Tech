"""
Orchestrator — Phase 3 / 13단계

사용자 입력을 라우팅한다:
  ├─ 일반 대화 (TaskIntent.GENERAL) → LLM 직답 (도구 미바인딩)
  ├─ tool_loop                    → registry 도구로 tool-calling
  └─ request_new_tool             → creation_pipeline (14~15단계에서 추가)

`pages/chat.py` 는 이제 `build_excel_agent` 를 직접 호출하지 않고
`orchestrator.run(...)` 만 호출한다.
"""
from __future__ import annotations

from typing import Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from agent.llm_factory import get_llm
from agent.prompt_enhancer import TaskIntent, classify_intent
from config.settings import settings


def is_simple_chat(intent: TaskIntent) -> bool:
    """일반 대화(GENERAL) 면 도구 바인딩 없이 LLM 직답한다."""
    return intent == TaskIntent.GENERAL


def _simple_chat(
    prompt: str,
    provider: str,
    model: str,
    temperature: float,
    chat_history: list[BaseMessage],
    task_guidance: str,
) -> dict:
    """도구 없이 LLM 직답 — 인사·소개·일반 질문 등 가벼운 응답."""
    llm = get_llm(provider, model, temperature=temperature)

    messages: list[BaseMessage] = [
        SystemMessage(
            content=(
                "당신은 친절한 한국어 AI 어시스턴트입니다. "
                "간결하고 정확하게 답하세요. 모르는 것은 모른다고 답하세요."
            )
        ),
    ]
    if task_guidance:
        messages.append(SystemMessage(content=task_guidance))
    messages.extend(chat_history)
    messages.append(HumanMessage(content=prompt))

    response = llm.invoke(messages)
    content = getattr(response, "content", str(response))
    return {"output": content, "route": "simple"}


def _tool_loop(
    prompt: str,
    provider: str,
    model: str,
    temperature: float,
    chat_history: list[BaseMessage],
    task_guidance: str,
) -> dict:
    """registry 의 도구로 tool-calling 에이전트를 돌린다."""
    # 13단계 — 기존 build_excel_agent 재사용 (시스템 프롬프트·_SYSTEM_PROMPT 그대로).
    # 14단계에서 request_new_tool 메타 도구가 추가될 때 이 부분도 분리될 예정.
    from agent.excel_agent import build_excel_agent

    agent_exec = build_excel_agent(provider, model, temperature)
    result = agent_exec.invoke(
        {
            "input": prompt,
            "chat_history": chat_history,
            "task_guidance": task_guidance,
        },
        config={"recursion_limit": 20},
    )
    return {
        "output": result.get("output", "처리 중 오류가 발생했습니다."),
        "route": "tool_loop",
    }


def run(
    prompt: str,
    *,
    provider: str = "ollama",
    model: Optional[str] = None,
    temperature: float = 0.0,
    chat_history: Optional[list[BaseMessage]] = None,
    task_guidance: str = "",
    intent: Optional[TaskIntent] = None,
) -> dict:
    """
    오케스트레이터 진입점.

    Args:
        prompt: 사용자 원문 (필요 시 첨부·태그 정보가 합쳐진 context)
        provider/model: LLM 프로바이더·모델. model 미지정 시 settings.DEFAULT_MODEL.
        temperature: LLM 온도
        chat_history: HumanMessage/AIMessage 리스트
        task_guidance: Phase 2 Prompt Enhancer 가 생성한 시스템 보강 지시
        intent: chat.py 가 이미 분류했으면 전달 (중복 분류 회피). 미지정 시
            orchestrator 가 규칙 기반으로 직접 분류.

    Returns:
        {"output": str, "route": "simple"|"tool_loop", ...}
    """
    if model is None:
        model = settings.DEFAULT_MODEL
    history = chat_history or []

    if intent is None:
        intent = classify_intent(prompt, use_llm_fallback=False).intent

    if is_simple_chat(intent):
        return _simple_chat(prompt, provider, model, temperature, history, task_guidance)

    return _tool_loop(prompt, provider, model, temperature, history, task_guidance)
