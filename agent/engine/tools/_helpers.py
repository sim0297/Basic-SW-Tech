"""도구 공통 헬퍼 — 파일 저장 + 결과 레지스트리 등록."""
from datetime import datetime
from pathlib import Path

import pandas as pd

from agent.engine.data import loader
from core.excel_processor import excel_processor


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_xlsx_and_register(df: pd.DataFrame, prefix: str = "result") -> Path:
    """DataFrame 을 results/<prefix>_<ts>.xlsx 로 저장 후 레지스트리에 등록."""
    filename = f"{prefix}_{now_stamp()}.xlsx"
    path = excel_processor.save_excel(df, filename)
    return loader.register_result(path)


def save_multi_sheet_and_register(sheets: dict, prefix: str = "통합결과") -> Path:
    """여러 시트 엑셀로 저장 후 레지스트리 등록."""
    filename = f"{prefix}_{now_stamp()}.xlsx"
    path = excel_processor.save_multi_sheet(sheets, filename)
    return loader.register_result(path)
