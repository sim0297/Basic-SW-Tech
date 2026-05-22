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
    set_file_scope,
)
from agent.prompt_enhancer import classify_intent, enhance
from config.settings import settings
from core.chat_manager import ChatSession, ChatStore
from core.file_manager import file_manager
from core.model_manager import CLOUD_MODELS, model_manager

st.title("💬 AI 채팅")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 결과 파일이 없을 때 답변에서 제거할 파일·시트 안내 키워드
_FILE_GUIDANCE_KEYWORDS = ("파일별비교", "시트를 확인", "시트에서 확인", "다운로드 버튼")


def _strip_file_guidance(text: str) -> str:
    """
    결과 파일이 생성되지 않은 응답에서 파일·시트 안내 문장을 제거한다.
    (에이전트가 통합도 안 했는데 "'파일별비교' 시트 확인" 같은 안내를 붙이는 것 방지)
    """
    kept = [
        line
        for line in text.split("\n")
        if not any(kw in line for kw in _FILE_GUIDANCE_KEYWORDS)
    ]
    return "\n".join(kept).strip()


def _render_enhance_info(info: dict | None) -> None:
    """프롬프트 보강 내역을 접기/펼치기로 표시한다 (투명성)."""
    if not info:
        return
    label = f"🔍 적용된 프롬프트 보강 — {info.get('intent', '')}"
    with st.expander(label, expanded=False):
        st.caption(f"의도 분류: {info.get('intent', '')} (via {info.get('method', '')})")
        st.code(info.get("text", ""), language="markdown")


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

chat_store = ChatStore()


def _activate_session(session: ChatSession) -> None:
    """세션을 현재 활성 채팅으로 설정한다 (URL ?chat=<id> 로 새로고침에도 유지)."""
    st.session_state.chat_session = session
    st.session_state.messages = session.messages
    st.query_params["chat"] = session.id


# ── 세션 초기화 / 복원 ────────────────────────────────────
# st.session_state 는 새로고침 시 사라지므로, URL 쿼리파라미터(?chat=<id>)와
# 디스크 저장본(chats/)으로 직전 대화를 복원한다.
if "chat_session" not in st.session_state:
    requested = st.query_params.get("chat")
    restored = chat_store.load(requested) if requested else None
    _activate_session(restored or ChatSession())

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

    enhance_on = st.toggle(
        "프롬프트 자동 보강",
        value=True,
        help="입력을 작업 유형에 맞는 persona·절차·제약을 갖춘 지시로 자동 보강합니다.",
    )

    st.caption(
        "엑셀/CSV 파일은 입력창의 📎 버튼으로 첨부하고, "
        "통합·분석·필터링을 자연어로 요청하세요."
    )

    st.divider()
    st.subheader("💬 채팅 기록")

    if st.button("➕ 새 채팅", width="stretch"):
        _activate_session(ChatSession(provider=provider, model=model))
        st.rerun()

    current_id = st.session_state.chat_session.id
    sessions = chat_store.list_sessions()
    if not sessions:
        st.caption("저장된 채팅이 없습니다.")
    for s in sessions:
        is_current = s["id"] == current_id
        col_load, col_del = st.columns([5, 1])
        with col_load:
            mark = "🟢 " if is_current else ""
            ts = s["created_at"][:16].replace("T", " ")
            if st.button(
                f"{mark}{s['title']}",
                key=f"chatload_{s['id']}",
                width="stretch",
                disabled=is_current,
                help=f"{ts} · 메시지 {s['count']}개",
            ):
                loaded = chat_store.load(s["id"])
                if loaded:
                    _activate_session(loaded)
                    st.rerun()
        with col_del:
            if st.button("🗑", key=f"chatdel_{s['id']}", help="이 채팅 삭제"):
                chat_store.delete(s["id"])
                if is_current:
                    _activate_session(ChatSession(provider=provider, model=model))
                st.rerun()

    st.divider()
    if st.button("💾 현재 채팅 MD 저장", width="stretch"):
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
        # 프롬프트 보강 내역도 재렌더링
        if msg.get("enhance"):
            _render_enhance_info(msg["enhance"])

# ── 파일 칩 태그 ──────────────────────────────────────────
# 파일명을 직접 타이핑하면 오타·표기 불일치로 못 찾으므로, 칩으로 정확히 지정.
_tag_options = [f.name for f in file_manager.list_files()]
tagged_files = (
    st.pills(
        "🏷 대상 파일 태그 — 클릭해 정확한 파일을 지정하세요 (선택)",
        _tag_options,
        selection_mode="multi",
        key="tagged_files",
    )
    if _tag_options
    else []
)

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

    # 첫 사용자 입력으로 채팅 제목 자동 설정
    session = st.session_state.chat_session
    if prompt_text and session.title == "새 채팅":
        session.title = prompt_text[:30]

    # 2) 사용자 메시지 표시
    prefixes = []
    if saved:
        prefixes.append(f"📎 첨부 업로드: {', '.join(saved)}")
    if tagged_files:
        prefixes.append(f"🏷 대상 파일: {', '.join(tagged_files)}")
    if prefixes:
        head = "\n".join(prefixes)
        user_display = f"{head}\n\n{prompt_text}" if prompt_text else head
    else:
        user_display = prompt_text

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
        chat_store.save(st.session_state.chat_session)  # 디스크 저장
        st.rerun()

    # 4) 지시가 있으면 에이전트 실행
    with st.chat_message("assistant"):
        set_file_manager(file_manager)
        reset_created_files()  # 이번 실행의 결과 파일 추적 시작
        # 칩으로 태그한 파일이 있으면 그 파일만 작업 대상으로 제한 (하드 제약)
        set_file_scope(tagged_files or None)

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
        if tagged_files:
            ctx_parts.append(
                f"대상 파일(정확한 파일명 — 이 파일들로 작업): {', '.join(tagged_files)}"
            )
        ctx_parts.append(prompt_text)
        ctx = "\n\n".join(ctx_parts)

        # 프롬프트 자동 보강 — 의도 분류 → 작업 유형 템플릿으로 보강 지시 생성
        task_guidance = ""
        enhance_info = None
        if enhance_on:
            has_files = bool(saved) or bool(file_manager.list_files())
            intent_result = classify_intent(prompt_text, has_files=has_files)
            file_names = [f.name for f in file_manager.list_files()]
            task_guidance = enhance(
                prompt_text,
                intent_result.intent,
                context={"file_names": file_names} if file_names else None,
            )
            enhance_info = {
                "intent": intent_result.intent.value,
                "method": intent_result.method,
                "text": task_guidance,
            }

        with st.spinner("처리 중..."):
            try:
                agent_exec = build_excel_agent(provider, model, temperature)
                result = agent_exec.invoke(
                    {
                        "input": ctx,
                        "chat_history": history,
                        "task_guidance": task_guidance,
                    },
                    config={"recursion_limit": 20},
                )
                answer = result.get("output", "처리 중 오류가 발생했습니다.")
            except Exception as e:
                answer = f"오류가 발생했습니다: {e}"

        created = get_created_files()  # 도구가 생성한 결과 파일 회수
        # 결과 파일이 없으면 파일·시트 안내 문장을 제거 (불필요한 안내 방지)
        if not created:
            answer = _strip_file_guidance(answer)
        st.markdown(answer)
        _render_result_files(
            created, key_prefix=f"dl_{len(st.session_state.messages)}"
        )
        _render_enhance_info(enhance_info)
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": answer,
                "files": [str(p) for p in created],
                "enhance": enhance_info,
            }
        )
        chat_store.save(st.session_state.chat_session)  # 디스크 저장
