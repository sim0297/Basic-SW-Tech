import pandas as pd
from langchain_core.tools import tool

from agent.engine.data import loader
from agent.engine.tools._helpers import save_xlsx_and_register
from core.excel_processor import excel_processor


@tool
def merge_files_concat(filenames: list[str]) -> str:
    """여러 파일을 단순히 행 방향으로 이어붙입니다. 결과는 results/ 폴더에 저장됩니다."""
    filenames = loader.scoped(filenames)
    dfs: list[pd.DataFrame] = []
    for name in filenames:
        df = loader.read_file(name)
        if df is not None:
            dfs.append(df)

    if not dfs:
        return "처리할 수 있는 파일이 없습니다."

    result_df = excel_processor.merge_concat(dfs)
    path = save_xlsx_and_register(result_df, prefix="merged_concat")
    return (
        f"병합 완료: **{path.name}**\n"
        f"  - 결과: {len(result_df)}행 × {len(result_df.columns)}열\n"
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다."
    )
