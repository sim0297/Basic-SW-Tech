import pandas as pd
from langchain_core.tools import tool

from agent.engine.data import loader
from agent.engine.tools._helpers import save_multi_sheet_and_register
from core.excel_processor import excel_processor


@tool
def merge_files_by_key(filenames: list[str], key_column: str = "") -> str:
    """
    [파일 통합의 기본 선택지]
    동일 양식의 여러 엑셀/CSV 파일을 '기준 컬럼(항목명)' 값이 같은 행끼리
    동일 항목으로 식별하여 통합한다. 행 순서나 항목 개수가 파일마다 달라도 안전하다.
    숫자 컬럼은 파일별 값의 평균, 텍스트 컬럼은 모두 같으면 유지·다르면 '값 상이',
    누락값은 'N/A'로 처리한다.
    결과는 3개 시트(통합결과·파일별비교·처리로그)를 가진 엑셀로 results/에 저장된다.
    key_column 미지정(빈 문자열) 시 항목명에 해당하는 컬럼을 자동 추정한다.

    "여러 파일을 하나로 통합", "동일 항목은 평균" 류의 요청은 거의 항상 이 도구를 쓴다.
    """
    filenames = loader.scoped(filenames)
    named: list[tuple[str, pd.DataFrame]] = []
    missing: list[str] = []
    for name in filenames:
        df = loader.read_file(name)
        if df is not None:
            named.append((name, df))
        else:
            missing.append(name)

    if not named:
        return "처리할 수 있는 파일이 없습니다."

    sheets = excel_processor.merge_by_key(named, key_col=key_column or None)
    path = save_multi_sheet_and_register(sheets, prefix="통합결과")

    result_df = sheets["통합결과"]
    log_df = sheets["처리로그"]
    mismatch = int((log_df["구분"] == "불일치").sum()) if not log_df.empty else 0
    miss = int((log_df["구분"] == "누락").sum()) if not log_df.empty else 0

    key_used = ""
    if not log_df.empty:
        kc = log_df[log_df["구분"] == "기준 컬럼"]
        if not kc.empty:
            key_used = str(kc.iloc[0]["항목"])

    msg = [
        f"통합 완료: **{path.name}**",
        f"  - 기준 컬럼: {key_used}" + (" (자동 추정)" if not key_column else ""),
        f"  - 통합 항목: {len(result_df)}개 × {len(result_df.columns)}열",
        f"  - 불일치(값 상이): {mismatch}건 / 누락 항목: {miss}건",
        "  - 시트 구성: 통합결과 · 파일별비교 · 처리로그",
        "  - 결과 파일은 채팅 답변 아래 다운로드 버튼으로 제공됩니다.",
    ]
    if missing:
        msg.append(f"  - 읽기 실패: {', '.join(missing)}")
    return "\n".join(msg)
