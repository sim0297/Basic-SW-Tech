import pandas as pd
from langchain_core.tools import tool

from agent.engine.data import loader
from agent.engine.tools._helpers import save_xlsx_and_register
from core.excel_processor import excel_processor


@tool
def merge_files_average(filenames: list[str]) -> str:
    """
    [거의 사용하지 않음 — 특수 케이스 전용]
    여러 파일을 '행 위치'(각 파일의 1번째 행끼리, 2번째 행끼리) 기준으로만
    평균 병합한다. 모든 파일의 행 순서와 개수가 완전히 동일할 때만 올바르다.
    '동일 항목'을 항목명으로 식별해 통합하려면 이 도구가 아니라
    merge_files_by_key 를 사용하라.
    결과는 results/ 폴더에 저장된다.
    """
    filenames = loader.scoped(filenames)
    dfs: list[pd.DataFrame] = []
    missing: list[str] = []
    for name in filenames:
        df = loader.read_file(name)
        if df is not None:
            dfs.append(df)
        else:
            missing.append(name)

    if not dfs:
        return "처리할 수 있는 파일이 없습니다."

    result_df = excel_processor.merge_average(dfs)
    path = save_xlsx_and_register(result_df, prefix="merged_average")

    msg = [
        f"병합 완료: **{path.name}**",
        f"  - 결과: {len(result_df)}행 × {len(result_df.columns)}열",
        f"  - 처리 파일: {', '.join(filenames)}",
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다.",
    ]
    if missing:
        msg.append(f"  - 읽기 실패: {', '.join(missing)}")
    return "\n".join(msg)
