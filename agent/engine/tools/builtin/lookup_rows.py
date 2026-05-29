from langchain_core.tools import tool

from agent.engine.data import loader


@tool
def lookup_rows(filename: str, value: str, column: str = "") -> str:
    """
    파일에서 특정 값과 일치하는 행을 찾아 그 행의 모든 컬럼 값을 반환합니다.
    "X 항목의 Y 값은?" 처럼 특정 항목을 조회하는 질문에 사용하세요.
    column 을 지정하면 그 컬럼에서만, 비워두면 모든 컬럼에서 value 를 검색합니다.
    (미리보기는 일부 행만 보이므로, 특정 항목 조회는 이 도구를 쓰세요.)
    """
    df = loader.read_file(filename)
    if df is None:
        return f"파일을 읽을 수 없습니다: {filename}"

    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    target = str(value)

    if column and column in df.columns:
        mask = df[column].astype(str).str.contains(target, case=False, na=False)
        scope = f"'{column}' 컬럼"
    else:
        mask = df.apply(
            lambda row: row.astype(str)
            .str.contains(target, case=False, na=False)
            .any(),
            axis=1,
        )
        scope = "전체 컬럼"

    hits = df[mask]
    if hits.empty:
        return f"'{value}' 와 일치하는 행이 없습니다 (검색 범위: {scope})."

    limit = 10
    lines = [f"'{value}' 검색 결과 — {len(hits)}개 행 (검색 범위: {scope})"]
    for idx, row in hits.head(limit).iterrows():
        lines.append(f"\n[행 {idx}]")
        for col in df.columns:
            lines.append(f"  {col}: {row[col]}")
    if len(hits) > limit:
        lines.append(f"\n... 외 {len(hits) - limit}개 행 생략")
    return "\n".join(lines)
