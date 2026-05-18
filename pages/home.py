import streamlit as st

st.title("🤖 KETI AI Platform")
st.caption("채팅창 하나로 엑셀 데이터를 통합·분석하는 AI 플랫폼")

st.markdown("---")

st.subheader("💬 채팅 — 모든 작업을 한 곳에서")
st.markdown("""
별도 페이지를 오갈 필요 없이, **채팅창 하나에서** 자연어 프롬프트로 모든 작업을 처리합니다.

- **파일 업로드** — 채팅 입력창의 📎 버튼으로 엑셀/CSV 첨부
- **통합·분석** — "5개 파일을 하나로 통합하고 동일 항목은 평균으로" 처럼 자연어로 요청
- **결과 다운로드** — 답변에 바로 생성되는 다운로드 버튼
- **다중 모델** — Ollama / OpenAI / Anthropic / Google 선택
""")

st.markdown("---")

st.subheader("🔧 보조 도구")
col1, col2 = st.columns(2)
with col1:
    st.markdown("""
    **📁 파일 보관함**
    업로드된 파일 조회·미리보기·삭제
    """)
with col2:
    st.markdown("""
    **📊 결과 보관함**
    생성된 결과 파일 모아보기
    """)

st.markdown("---")

st.subheader("🚀 사용 예시")
st.markdown("""
1. **💬 채팅** 으로 이동합니다.
2. 입력창의 📎 버튼으로 동일 양식의 엑셀 파일 여러 개를 첨부합니다.
3. 프롬프트를 입력합니다:
   > "첨부한 파일들을 하나로 통합하고, 동일 항목은 평균값으로 계산해줘"
4. 답변에 표시되는 **⬇ 다운로드 버튼**으로 통합 결과(3개 시트 엑셀)를 받습니다.
""")

st.info("왼쪽 사이드바의 **채팅** 에서 바로 시작하세요.")
