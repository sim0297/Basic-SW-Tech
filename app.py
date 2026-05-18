import streamlit as st

st.set_page_config(
    page_title="sheets.ai",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 공통 세션 상태 초기화 ─────────────────────────────────
if "initialized" not in st.session_state:
    st.session_state.initialized = True
    st.session_state.messages = []
    st.session_state.selected_provider = "ollama"
    st.session_state.selected_model = "qwen3:14b"
    st.session_state.temperature = 0.1

# ── 페이지 정의 ───────────────────────────────────────────
#   메인: 채팅 하나로 업로드·통합·분석·다운로드를 모두 처리
#   보조: 조회·관리 및 모델 설정용 화면
nav = st.navigation(
    {
        "메인": [
            st.Page("pages/home.py", title="홈", icon="🏠"),
            st.Page("pages/chat.py", title="채팅", icon="💬", default=True),
        ],
        "보조 도구": [
            st.Page("pages/files.py", title="파일 보관함", icon="📁"),
            st.Page("pages/results.py", title="결과 보관함", icon="📊"),
        ],
    }
)
nav.run()
