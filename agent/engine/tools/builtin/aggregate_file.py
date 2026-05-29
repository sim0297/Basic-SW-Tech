from langchain_core.tools import tool

from agent.engine.data import loader
from agent.engine.tools._helpers import save_xlsx_and_register


@tool
def aggregate_file(
    filename: str, group_by: str, agg_columns: list[str], method: str = "mean"
) -> str:
    """
    파일을 특정 컬럼으로 그룹화하고 집계합니다.
    method: 'mean', 'sum', 'count', 'min', 'max'
    결과는 results/ 폴더에 저장됩니다.
    """
    df = loader.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"

    valid_methods = {"mean", "sum", "count", "min", "max"}
    if method not in valid_methods:
        return f"지원하지 않는 집계 방법: {method}. 사용 가능: {', '.join(valid_methods)}"

    try:
        cols_to_use = [c for c in agg_columns if c in df.columns]
        if not cols_to_use:
            cols_to_use = df.select_dtypes(include="number").columns.tolist()

        result_df = df.groupby(group_by)[cols_to_use].agg(method).reset_index()
    except Exception as e:
        return f"집계 오류: {e}"

    path = save_xlsx_and_register(result_df, prefix="aggregated")
    return (
        f"집계 완료: **{path.name}**\n"
        f"  - 그룹: {group_by} / 집계: {method}\n"
        f"  - 결과: {len(result_df)}행\n"
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다."
    )
