from langchain_core.tools import tool

from agent.engine.data import loader
from core.excel_processor import excel_processor


@tool
def read_file_preview(filename: str) -> str:
    """파일의 컬럼 정보와 상위 20행 미리보기를 반환합니다."""
    df = loader.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"
    return excel_processor.describe_df(df, max_rows=20)
