import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from core.file_manager import file_manager

st.title("📁 파일 보관함")
st.caption(
    "업로드된 파일을 조회·미리보기·삭제하는 보조 화면입니다. "
    "파일 업로드와 통합·분석은 💬 채팅에서 자연어로 수행하세요."
)

# ── 파일 목록 ─────────────────────────────────────────────
files = file_manager.list_files()

if not files:
    st.info("저장된 파일이 없습니다. 채팅 화면에서 파일을 첨부해 업로드하세요.")
else:
    st.caption(f"총 {len(files)}개 파일")

    for fi in files:
        with st.container(border=True):
            col_name, col_info, col_actions = st.columns([3, 3, 2])

            with col_name:
                st.markdown(f"**{fi.name}**")
                st.caption(fi.uploaded_at[:19].replace("T", " "))

            with col_info:
                col_r, col_c, col_s = st.columns(3)
                col_r.metric("행", f"{fi.rows:,}")
                col_c.metric("열", f"{fi.cols:,}")
                col_s.metric("크기", f"{fi.size_kb:.1f} KB")

            with col_actions:
                btn_col1, btn_col2, btn_col3 = st.columns(3)

                with btn_col1:
                    if st.button("👁", key=f"prev_{fi.name}", help="미리보기"):
                        st.session_state[f"show_preview_{fi.name}"] = not st.session_state.get(
                            f"show_preview_{fi.name}", False
                        )

                with btn_col2:
                    st.download_button(
                        "⬇",
                        data=fi.path.read_bytes(),
                        file_name=fi.name,
                        key=f"dl_{fi.name}",
                        help="다운로드",
                    )

                with btn_col3:
                    if st.button("🗑", key=f"del_{fi.name}", help="삭제", type="primary"):
                        st.session_state[f"confirm_del_{fi.name}"] = True

            # 삭제 확인
            if st.session_state.get(f"confirm_del_{fi.name}"):
                st.warning(f"**{fi.name}** 을(를) 삭제하시겠습니까?")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("확인 삭제", key=f"confirm_yes_{fi.name}", type="primary"):
                        file_manager.delete_file(fi.name)
                        st.session_state.pop(f"confirm_del_{fi.name}", None)
                        st.rerun()
                with c2:
                    if st.button("취소", key=f"confirm_no_{fi.name}"):
                        st.session_state.pop(f"confirm_del_{fi.name}", None)
                        st.rerun()

            # 미리보기
            if st.session_state.get(f"show_preview_{fi.name}"):
                df = file_manager.read_file(fi.name)
                if df is not None:
                    with st.expander(f"📊 {fi.name} 미리보기", expanded=True):
                        tab_data, tab_info = st.tabs(["데이터", "컬럼 정보"])
                        with tab_data:
                            st.dataframe(df.head(50), width="stretch", height=300)
                        with tab_info:
                            info_df = pd.DataFrame({
                                "컬럼명": df.columns.astype(str),
                                "타입": df.dtypes.astype(str).values,
                                "null 수": df.isnull().sum().values,
                                "null %": (df.isnull().mean() * 100).round(1).values,
                            })
                            st.dataframe(info_df, width="stretch")
                else:
                    st.error(f"{fi.name} 파일을 읽을 수 없습니다.")
