from langchain_core.tools import tool

from agent.engine.data import loader
from agent.engine.tools._helpers import save_xlsx_and_register


@tool
def filter_file(filename: str, column: str, operator: str, value: str) -> str:
    """
    파일에서 특정 조건으로 행을 필터링합니다.
    operator: '==', '!=', '>', '>=', '<', '<='
    결과는 results/ 폴더에 저장됩니다.
    """
    df = loader.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"
    if column not in df.columns:
        return f"컬럼 '{column}'이 존재하지 않습니다. 사용 가능: {', '.join(df.columns)}"

    try:
        try:
            num_val = float(value)
            ops = {
                "==": df[df[column] == num_val],
                "!=": df[df[column] != num_val],
                ">":  df[df[column] >  num_val],
                ">=": df[df[column] >= num_val],
                "<":  df[df[column] <  num_val],
                "<=": df[df[column] <= num_val],
            }
        except ValueError:
            ops = {
                "==": df[df[column] == value],
                "!=": df[df[column] != value],
            }
        result_df = ops.get(operator)
        if result_df is None:
            return f"지원하지 않는 연산자: {operator}"
    except Exception as e:
        return f"필터 오류: {e}"

    path = save_xlsx_and_register(result_df, prefix="filtered")
    return (
        f"필터 결과: **{path.name}**\n"
        f"  - 조건: {column} {operator} {value}\n"
        f"  - 결과: {len(result_df)}행\n"
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다."
    )
