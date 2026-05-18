import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage

from agent.excel_agent import (
    build_excel_agent,
    get_created_files,
    reset_created_files,
    set_file_manager,
)
from config.settings import settings
from core.chat_manager import ChatSession
from core.file_manager import file_manager
from core.model_manager import CLOUD_MODELS, model_manager

st.title("💬 AI 채팅")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _render_result_files(paths: list, key_prefix: str) -> None:
    """에이전트가 생성한 결과 파일의 다운로드 버튼을 렌더링한다."""
    if not paths:
        return
    st.markdown("**📎 생성된 결과 파일**")
    for i, raw in enumerate(paths):
        p = Path(raw)
        if not p.exists():
            st.caption(f"⚠️ {p.name} — 파일이 만료되어 다운로드할 수 없습니다.")
            continue
        size_kb = p.stat().st_size / 1024
        st.download_button(
            f"⬇ {p.name}  ({size_kb:.1f} KB)",
            data=p.read_bytes(),
            file_name=p.name,
            mime=_XLSX_MIME if p.suffix == ".xlsx" else "text/plain",
            key=f"{key_prefix}_{i}",
            type="primary",
        )

# ── 세션 초기화 ───────────────────────────────────────────
if "messages" not in st.session_state:
    st.session_state.messages = []
if "chat_session" not in st.session_state:
    st.session_state.chat_session = ChatSession()

# ── 사이드바 ──────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 모델 설정")

    all_providers = ["ollama"] + list(CLOUD_MODELS.keys())
    provider = st.selectbox("프로바이더", all_providers, key="chat_provider")

    if provider == "ollama":
        ollama_models = [m["name"] for m in model_manager.list_models()]
        if not ollama_models:
            st.warning("Ollama 모델이 없습니다. 터미널에서 `ollama pull qwen3:14b` 로 받으세요.")
            ollama_models = [settings.DEFAULT_MODEL]
        # 기본 모델(settings.DEFAULT_MODEL)이 설치돼 있으면 그것을 기본 선택
        default_idx = (
            ollama_models.index(settings.DEFAULT_MODEL)
            if settings.DEFAULT_MODEL in ollama_models
            else 0
        )
        model = st.selectbox(
            "모델", ollama_models, index=default_idx, key="chat_model"
        )
    else:
        cloud_models = CLOUD_MODELS.get(provider, [])
        model = st.selectbox("모델", cloud_models, key="chat_model_cloud")

    temperature = st.slider("Temperature", 0.0, 1.0, 0.1, 0.05, key="chat_temp")

    st.divider()
    st.caption(
        "엑셀/CSV 파일은 입력창의 📎 버튼으로 첨부하고, "
        "통합·분석·필터링을 자연어로 요청하세요."
    )

    col_new, col_save = st.columns(2)
    with col_new:
        if st.button("🗑 새 채팅", width="stretch"):
            st.session_state.messages = []
            st.session_state.chat_session = ChatSession(
                provider=provider, model=model
            )
            st.rerun()
    with col_save:
        if st.button("💾 MD 저장", width="stretch"):
            session = st.session_state.chat_session
            session.messages = st.session_state.messages
            path = session.save_as_md()
            st.success(f"저장: {path.name}")

# ── 채팅 메시지 출력 ──────────────────────────────────────
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        # 결과 파일이 첨부된 메시지는 다운로드 버튼도 재생성 (재렌더링 후에도 유지)
        if msg.get("files"):
            _render_result_files(msg["files"], key_prefix=f"hist_{idx}")

# ── 사용자 입력 (텍스트 + 파일 첨부) ──────────────────────
chat_in = st.chat_input(
    "메시지 입력 — 엑셀/CSV 파일을 함께 첨부할 수 있습니다",
    accept_file="multiple",
    file_type=["csv", "xlsx", "xls", "parquet"],
)

if chat_in:
    prompt_text = (chat_in.text or "").strip()
    attached = chat_in.files or []

    # 1) 첨부 파일 저장
    saved: list[str] = []
    for f in attached:
        file_manager.save_file(f)
        saved.append(f.name)

    # 2) 사용자 메시지 표시
    user_display = prompt_text
    if saved:
        prefix = f"📎 첨부 업로드: {', '.join(saved)}"
        user_display = f"{prefix}\n\n{prompt_text}" if prompt_text else prefix

    st.session_state.messages.append({"role": "user", "content": user_display})
    with st.chat_message("user"):
        st.markdown(user_display)

    # 3) 파일만 첨부하고 지시가 없으면 업로드 안내만
    if not prompt_text:
        ack = (
            f"✅ {len(saved)}개 파일이 업로드되었습니다: {', '.join(saved)}\n\n"
            "이제 통합·분석·필터링 등을 자연어로 요청하세요."
            if saved
            else "메시지를 입력해 주세요."
        )
        with st.chat_message("assistant"):
            st.markdown(ack)
        st.session_state.messages.append({"role": "assistant", "content": ack})
        st.rerun()

    # 4) 지시가 있으면 에이전트 실행
    with st.chat_message("assistant"):
        set_file_manager(file_manager)
        reset_created_files()  # 이번 실행의 결과 파일 추적 시작

        # 직전까지의 대화 히스토리 (방금 추가한 사용자 입력 제외)
        history = []
        for msg in st.session_state.messages[:-1]:
            if msg["role"] == "user":
                history.append(HumanMessage(content=msg["content"]))
            elif msg["role"] == "assistant":
                history.append(AIMessage(content=msg["content"]))

        ctx_parts = []
        if saved:
            ctx_parts.append(f"방금 업로드된 파일: {', '.join(saved)}")
        ctx_parts.append(prompt_text)
        ctx = "\n\n".join(ctx_parts)

        with st.spinner("처리 중..."):
            try:
                agent_exec = build_excel_agent(provider, model, temperature)
                result = agent_exec.invoke(
                    {"input": ctx, "chat_history": history},
                    config={"recursion_limit": 20},
                )
                answer = result.get("output", "처리 중 오류가 발생했습니다.")
            except Exception as e:
                answer = f"오류가 발생했습니다: {e}"

        created = get_created_files()  # 도구가 생성한 결과 파일 회수
        st.markdown(answer)
        _render_result_files(
            created, key_prefix=f"dl_{len(st.session_state.messages)}"
        )
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer,
                "files": [str(p) for p in created],
            }
        )
