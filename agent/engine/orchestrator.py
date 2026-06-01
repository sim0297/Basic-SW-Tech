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


def _generated_tools_used(intermediate_steps: list) -> list[str]:
    """AgentExecutor 의 intermediate_steps 에서 호출된 generated 도구명을 추린다."""
    if not intermediate_steps:
        return []
    from agent.engine.tool_registry import manager

    used: list[str] = []
    for step in intermediate_steps:
        action = step[0] if isinstance(step, (tuple, list)) and step else step
        tool_name = getattr(action, "tool", None)
        if not tool_name or tool_name in used:
            continue
        entry = manager.find(tool_name)
        if entry and entry.get("source") == "generated":
            used.append(tool_name)
    return used


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
    """
    registry 의 도구로 tool-calling 에이전트를 돌린다.

    Phase 3 / 14단계 — agent 가 `request_new_tool` 메타 도구를 호출하면
    creation_pipeline 으로 분기한다 (한 요청당 1회).
    """
    from agent.engine import creation_pipeline
    from agent.excel_agent import build_excel_agent

    # 이전 요청의 잔재 제거 — 한 요청당 새 도구 생성 시도 1회 보장
    creation_pipeline.reset_pending()

    agent_exec = build_excel_agent(provider, model, temperature)
    result = agent_exec.invoke(
        {
            "input": prompt,
            "chat_history": chat_history,
            "task_guidance": task_guidance,
        },
        config={"recursion_limit": 20},
    )
    answer = result.get("output", "처리 중 오류가 발생했습니다.")

    # request_new_tool 이 호출됐는지 가로채기 → creation_pipeline 으로 분기
    pending_intent = creation_pipeline.get_pending()
    if pending_intent:
        creation_output = creation_pipeline.run(pending_intent)
        creation_pipeline.reset_pending()
        return {
            "output": creation_output,
            "route": "creation_pipeline",
            "user_intent": pending_intent,
        }

    # 20단계 — 이번 답변에 자동 생성(generated) 도구가 쓰였는지 추적 (투명성 배지용)
    generated_used = _generated_tools_used(result.get("intermediate_steps", []))
    return {
        "output": answer,
        "route": "tool_loop",
        "generated_tools": generated_used,
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
