import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import streamlit as st

from config.settings import settings

st.title("📊 결과 보관함")
st.caption(
    "에이전트가 생성한 결과 파일을 모아 보는 보조 화면입니다. "
    "최신 결과는 💬 채팅의 답변에서 바로 다운로드할 수 있습니다."
)


def _list_results() -> list[Path]:
    results_dir = settings.RESULTS_DIR
    patterns = ["*.md", "*.xlsx", "*.csv"]
    files: list[Path] = []
    for pat in patterns:
        files.extend(results_dir.glob(pat))
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _file_size_str(path: Path) -> str:
    size = path.stat().st_size
    if size < 1024:
        return f"{size} B"
    elif size < 1024**2:
        return f"{size/1024:.1f} KB"
    return f"{size/1024**2:.1f} MB"


def _read_df(path: Path) -> pd.DataFrame | None:
    try:
        if path.suffix == ".xlsx":
            return pd.read_excel(path)
        elif path.suffix == ".csv":
            return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return None
    return None


# ── 새로 고침 버튼 ─────────────────────────────────────────
col_title, col_refresh = st.columns([5, 1])
with col_refresh:
    if st.button("🔄 새로고침", width="stretch"):
        st.rerun()

files = _list_results()

if not files:
    st.info("저장된 결과 파일이 없습니다.")
    st.markdown(
        "채팅 페이지에서 **MD 저장** 버튼을 누르거나 "
        "엑셀 처리 에이전트를 실행하면 결과가 여기에 표시됩니다."
    )
else:
    st.caption(f"총 {len(files)}개 결과 파일")

    for fpath in files:
        ext = fpath.suffix.lower()
        icon = "📝" if ext == ".md" else "📊"
        mtime = pd.Timestamp(fpath.stat().st_mtime, unit="s").strftime("%Y-%m-%d %H:%M:%S")

        with st.container(border=True):
            col_n, col_m, col_s, col_act = st.columns([4, 2, 1, 2])

            with col_n:
                st.markdown(f"{icon} **{fpath.name}**")
            with col_m:
                st.caption(mtime)
            with col_s:
                st.caption(_file_size_str(fpath))
            with col_act:
                btn1, btn2, btn3 = st.columns(3)
                with btn1:
                    if st.button("👁", key=f"prev_{fpath.name}", help="미리보기"):
                        cur = st.session_state.get(f"show_{fpath.name}", False)
                        st.session_state[f"show_{fpath.name}"] = not cur
                with btn2:
                    st.download_button(
                        "⬇",
                        data=fpath.read_bytes(),
                        file_name=fpath.name,
                        key=f"dl_{fpath.name}",
                        help="다운로드",
                    )
                with btn3:
                    if st.button("🗑", key=f"del_{fpath.name}", help="삭제", type="primary"):
                        st.session_state[f"confirm_{fpath.name}"] = True

            # 삭제 확인
            if st.session_state.get(f"confirm_{fpath.name}"):
                st.warning(f"**{fpath.name}** 을(를) 삭제하시겠습니까?")
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("삭제 확인", key=f"yes_{fpath.name}", type="primary"):
                        fpath.unlink(missing_ok=True)
                        st.session_state.pop(f"confirm_{fpath.name}", None)
                        st.rerun()
                with c2:
                    if st.button("취소", key=f"no_{fpath.name}"):
                        st.session_state.pop(f"confirm_{fpath.name}", None)
                        st.rerun()

            # 미리보기
            if st.session_state.get(f"show_{fpath.name}"):
                with st.expander(f"미리보기: {fpath.name}", expanded=True):
                    if ext == ".md":
                        content = fpath.read_text(encoding="utf-8")
                        st.markdown(content)
                    elif ext in {".xlsx", ".csv"}:
                        df = _read_df(fpath)
                        if df is not None:
                            st.dataframe(df, width="stretch", height=300)
                            st.caption(
                                f"{len(df):,}행 × {len(df.columns)}열 | "
                                f"컬럼: {', '.join(df.columns.tolist())}"
                            )
                        else:
                            st.error("파일을 읽을 수 없습니다.")
