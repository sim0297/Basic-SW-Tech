import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import settings

ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet"}


@dataclass
class FileInfo:
    name: str
    path: Path
    size: int
    uploaded_at: str
    rows: int = 0
    cols: int = 0

    @property
    def size_kb(self) -> float:
        return self.size / 1024

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "uploaded_at": self.uploaded_at,
            "rows": self.rows,
            "cols": self.cols,
        }


class FileManager:
    _META_FILE = "metadata.json"

    def __init__(self, upload_dir: Optional[Path] = None):
        self.upload_dir = upload_dir or settings.UPLOAD_DIR
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path = self.upload_dir / self._META_FILE

    def _load_meta(self) -> dict:
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_meta(self, meta: dict) -> None:
        self._meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def save_file(self, uploaded_file) -> FileInfo:
        dest = self.upload_dir / uploaded_file.name
        dest.write_bytes(uploaded_file.getvalue())

        df = self._read_df(dest)
        rows, cols = (len(df), len(df.columns)) if df is not None else (0, 0)

        meta = self._load_meta()
        meta[uploaded_file.name] = {
            "name": uploaded_file.name,
            "size": dest.stat().st_size,
            "uploaded_at": datetime.now().isoformat(),
            "rows": rows,
            "cols": cols,
        }
        self._save_meta(meta)

        return FileInfo(
            name=uploaded_file.name,
            path=dest,
            size=dest.stat().st_size,
            uploaded_at=meta[uploaded_file.name]["uploaded_at"],
            rows=rows,
            cols=cols,
        )

    def list_files(self) -> list[FileInfo]:
        meta = self._load_meta()
        result = []
        for name, info in meta.items():
            path = self.upload_dir / name
            if path.exists():
                result.append(
                    FileInfo(
                        name=name,
                        path=path,
                        size=info.get("size", 0),
                        uploaded_at=info.get("uploaded_at", ""),
                        rows=info.get("rows", 0),
                        cols=info.get("cols", 0),
                    )
                )
        return result

    def delete_file(self, name: str) -> bool:
        path = self.upload_dir / name
        if path.exists():
            path.unlink()
        meta = self._load_meta()
        if name in meta:
            del meta[name]
            self._save_meta(meta)
        return True

    def read_file(self, name: str) -> Optional[pd.DataFrame]:
        path = self.upload_dir / name
        if not path.exists():
            return None
        return self._read_df(path)

    def get_file_path(self, name: str) -> Optional[Path]:
        path = self.upload_dir / name
        return path if path.exists() else None

    def _read_df(self, path: Path) -> Optional[pd.DataFrame]:
        suffix = path.suffix.lower()
        try:
            if suffix == ".csv":
                for enc in ["utf-8", "cp949", "euc-kr", "latin-1"]:
                    try:
                        return self._coerce_numeric(pd.read_csv(path, encoding=enc))
                    except (UnicodeDecodeError, Exception):
                        continue
            elif suffix in {".xlsx", ".xls"}:
                df = self._read_excel_smart(path)
                return self._coerce_numeric(df) if df is not None else None
            elif suffix == ".parquet":
                return pd.read_parquet(path)
        except Exception:
            return None
        return None

    @staticmethod
    def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
        """
        '54,684,000' 처럼 천단위 콤마가 섞인 문자열 숫자 컬럼을 실제 숫자형으로 변환한다.
        내용이 있는 셀의 90% 이상이 숫자로 변환되는 컬럼만 대상으로 한다.
        """
        if df is None:
            return df
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            raw = df[col].astype(str).str.strip()
            has_value = raw.ne("") & raw.str.lower().ne("nan")
            if has_value.sum() == 0:
                continue
            converted = pd.to_numeric(
                raw.str.replace(",", "", regex=False), errors="coerce"
            )
            ok = converted.notna() & has_value
            if ok.sum() / has_value.sum() >= 0.9:
                df[col] = converted
        return df

    @staticmethod
    def _is_unnamed(col: str) -> bool:
        s = str(col).strip()
        return s == "" or s.lower() == "nan" or s.startswith("Unnamed")

    def _read_excel_smart(self, path: Path) -> Optional[pd.DataFrame]:
        """
        엑셀의 병합된 다중 행 헤더를 자동 감지하여 평탄화한다.
        1행 헤더로 읽었을 때 'Unnamed' 컬럼이 2개 이상이면 2행 헤더로 재시도.
        """
        df = pd.read_excel(path)
        unnamed = sum(1 for c in df.columns if self._is_unnamed(c))
        if unnamed < 2:
            return df

        try:
            df2 = pd.read_excel(path, header=[0, 1])
            flat = self._flatten_columns(df2.columns)
            unnamed2 = sum(1 for c in flat if self._is_unnamed(c))
            # 다중 헤더로 읽었을 때 빈 컬럼이 줄어든 경우에만 채택
            if unnamed2 < unnamed:
                df2.columns = flat
                return df2
        except Exception:
            pass
        return df

    @staticmethod
    def _flatten_columns(cols) -> list[str]:
        """MultiIndex 컬럼을 '상위_하위' 형태의 단일 문자열로 평탄화 (중복은 접미사 부여)."""
        flat: list[str] = []
        for col in cols:
            parts = col if isinstance(col, tuple) else (col,)
            kept = [
                str(p).strip()
                for p in parts
                if not FileManager._is_unnamed(p)
            ]
            flat.append("_".join(kept) if kept else "column")

        seen: dict[str, int] = {}
        result: list[str] = []
        for name in flat:
            if name in seen:
                seen[name] += 1
                result.append(f"{name}.{seen[name]}")
            else:
                seen[name] = 0
                result.append(name)
        return result


file_manager = FileManager()
