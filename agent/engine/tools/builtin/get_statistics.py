from langchain_core.tools import tool

from agent.engine.data import loader


@tool
def get_statistics(filename: str) -> str:
    """파일의 수치형 컬럼에 대한 통계 요약(평균, 표준편차, 최솟값, 최댓값 등)을 반환합니다."""
    df = loader.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"
    return df.describe(include="all").to_string()
