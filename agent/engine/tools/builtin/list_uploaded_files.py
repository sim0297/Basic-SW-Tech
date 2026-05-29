from langchain_core.tools import tool

from agent.engine.data import loader


@tool
def list_uploaded_files() -> str:
    """업로드된 파일 목록과 메타데이터(행수·열수·크기)를 반환합니다."""
    files = loader.list_files()
    if not files:
        return "업로드된 파일이 없습니다."
    lines = []
    if loader.scope_active():
        lines.append(
            "⚠️ 사용자가 아래 파일만 작업 대상으로 지정했습니다. "
            "다른 파일은 절대 사용하지 말고, 이 목록에 있는 파일만 처리하세요."
        )
    for f in files:
        lines.append(
            f"- {f.name}: {f.rows}행 × {f.cols}열 / {f.size_kb:.1f}KB / "
            f"업로드: {f.uploaded_at[:10]}"
        )
    return "\n".join(lines)
