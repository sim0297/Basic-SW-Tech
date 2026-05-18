from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import settings


class ExcelProcessor:
    @staticmethod
    def merge_average(dfs: list[pd.DataFrame]) -> pd.DataFrame:
        """모든 파일의 공통 수치 컬럼을 행 단위 평균으로 병합합니다."""
        if not dfs:
            return pd.DataFrame()
        if len(dfs) == 1:
            return dfs[0].copy()

        result = dfs[0].copy()
        ref_len = len(dfs[0])
        same_len = all(len(df) == ref_len for df in dfs)

        for col in dfs[0].columns:
            available = [df for df in dfs if col in df.columns]
            if not available:
                continue

            if pd.api.types.is_numeric_dtype(dfs[0][col]) and same_len:
                stacked = pd.concat(
                    [df[col].reset_index(drop=True) for df in available], axis=1
                )
                result[col] = stacked.mean(axis=1)

        return result

    @staticmethod
    def merge_concat(dfs: list[pd.DataFrame]) -> pd.DataFrame:
        """여러 DataFrame을 단순 행 방향으로 이어붙입니다."""
        return pd.concat(dfs, ignore_index=True)

    _KEY_NAME_HINTS = ("항목", "명칭", "이름", "분류", "품목", "코드", "명")

    @staticmethod
    def infer_key_column(df: pd.DataFrame) -> str:
        """
        기준 컬럼 자동 추정.
        - 컬럼명에 '항목/명칭/이름/분류/품목/코드/명' 포함 시 가산점
        - 결측이 적고(coverage) 고유값이 많은(uniqueness) 컬럼 우대
        - 숫자형 컬럼은 키로 부적합하므로 감점
        """
        cols = [str(c) for c in df.columns]
        n = max(len(df), 1)
        best, best_score = cols[0], float("-inf")
        for c in cols:
            s = df[c]
            non_null = int(s.notna().sum())
            if non_null == 0:
                continue
            name_bonus = (
                2.0 if any(h in c for h in ExcelProcessor._KEY_NAME_HINTS) else 0.0
            )
            numeric_penalty = (
                1.0 if pd.api.types.is_numeric_dtype(s) else 0.0
            )
            coverage = non_null / n
            uniqueness = s.nunique(dropna=True) / n
            score = name_bonus + coverage + uniqueness - numeric_penalty
            if score > best_score:
                best_score, best = score, c
        return best

    @staticmethod
    def merge_by_key(
        named_dfs: list[tuple[str, pd.DataFrame]],
        key_col: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        """
        동일 양식의 여러 파일을 '기준 컬럼' 값 기준으로 통합한다.

        - 기준 컬럼이 같은 행 = 동일 항목
        - 숫자 컬럼: 파일별 값의 평균
        - 텍스트 컬럼: 모두 같으면 유지, 다르면 '값 상이'
        - 누락(키 없음 또는 빈 값): 'N/A'

        반환: {"통합결과": df, "파일별비교": df, "처리로그": df}
        """
        named_dfs = [
            (str(n), d) for n, d in named_dfs if d is not None and not d.empty
        ]
        empty = pd.DataFrame()
        if not named_dfs:
            return {"통합결과": empty, "파일별비교": empty.copy(), "처리로그": empty.copy()}

        # 모든 컬럼명을 문자열로 정규화
        norm = []
        for name, df in named_dfs:
            d = df.copy()
            d.columns = [str(c) for c in d.columns]
            norm.append((name, d))
        named_dfs = norm

        first_df = named_dfs[0][1]
        if not key_col or key_col not in first_df.columns:
            key_col = ExcelProcessor.infer_key_column(first_df)
            key_note = "자동 추정된 기준 컬럼 — 동일 항목 식별"
        else:
            key_note = "지정된 기준 컬럼 — 동일 항목 식별"

        log_rows: list[dict] = [
            {"구분": "기준 컬럼", "항목": key_col, "내용": key_note}
        ]

        # 데이터 컬럼(기준 컬럼 제외) — 등장 순서 보존하며 합집합
        data_cols: list[str] = []
        for _, df in named_dfs:
            for c in df.columns:
                if c != key_col and c not in data_cols:
                    data_cols.append(c)

        # 컬럼별 숫자 여부 (과반 파일에서 숫자형이면 숫자 컬럼)
        col_numeric: dict[str, bool] = {}
        for c in data_cols:
            votes = total = 0
            for _, df in named_dfs:
                if c in df.columns:
                    total += 1
                    if pd.api.types.is_numeric_dtype(df[c]):
                        votes += 1
            col_numeric[c] = total > 0 and votes >= total / 2

        # 파일별 {키: {컬럼: 값}} 맵 — 한 파일 내 중복 키는 숫자=평균/텍스트=첫값
        file_maps: dict[str, dict] = {}
        all_keys: list[str] = []
        seen: set[str] = set()
        for name, df in named_dfs:
            if key_col not in df.columns:
                log_rows.append(
                    {"구분": "경고", "항목": name, "내용": f"기준 컬럼 '{key_col}' 없음 — 제외"}
                )
                continue
            keys_norm = df[key_col].astype(str).str.strip()
            valid = keys_norm.ne("") & keys_norm.str.lower().ne("nan")
            skipped = int((~valid).sum())
            fmap: dict[str, dict] = {}
            for k, g in df[valid].groupby(keys_norm[valid]):
                row = {}
                for c in data_cols:
                    if c not in g.columns:
                        row[c] = None
                        continue
                    vals = g[c].dropna()
                    if vals.empty:
                        row[c] = None
                    elif col_numeric[c]:
                        nums = pd.to_numeric(vals, errors="coerce").dropna()
                        row[c] = float(nums.mean()) if not nums.empty else None
                    else:
                        row[c] = vals.iloc[0]
                fmap[str(k)] = row
                if str(k) not in seen:
                    seen.add(str(k))
                    all_keys.append(str(k))
            file_maps[name] = fmap
            note = f"{len(df)}행, 유효 항목 {len(fmap)}개"
            if skipped:
                note += f", 빈 키 {skipped}행 제외"
            log_rows.append({"구분": "파일 읽기", "항목": name, "내용": note})

        file_names = [n for n, _ in named_dfs if n in file_maps]

        # 항목별 누락 로그
        for k in all_keys:
            absent = [n for n in file_names if k not in file_maps.get(n, {})]
            if absent and len(absent) < len(file_names):
                log_rows.append(
                    {"구분": "누락", "항목": k, "내용": f"미포함 파일: {', '.join(absent)}"}
                )

        # 통합결과 / 파일별비교 구성
        result_rows: list[dict] = []
        compare_rows: list[dict] = []
        for k in all_keys:
            res = {key_col: k}
            for c in data_cols:
                per_file = []
                for n in file_names:
                    v = file_maps.get(n, {}).get(k, {}).get(c)
                    per_file.append(v)
                present = [v for v in per_file if v is not None and not pd.isna(v)]

                if col_numeric[c]:
                    merged = round(sum(present) / len(present), 2) if present else "N/A"
                else:
                    uniq = list(dict.fromkeys(str(v).strip() for v in present))
                    if not uniq:
                        merged = "N/A"
                    elif len(uniq) == 1:
                        merged = uniq[0]
                    else:
                        merged = "값 상이"
                        log_rows.append(
                            {
                                "구분": "불일치",
                                "항목": f"{k} / {c}",
                                "내용": f"값 상이: {uniq}",
                            }
                        )
                res[c] = merged

                crow = {"항목": k, "컬럼": c}
                for n, v in zip(file_names, per_file):
                    crow[n] = "N/A" if (v is None or pd.isna(v)) else v
                crow["통합값"] = merged
                compare_rows.append(crow)
            result_rows.append(res)

        result_df = pd.DataFrame(result_rows, columns=[key_col] + data_cols)
        compare_df = pd.DataFrame(
            compare_rows, columns=["항목", "컬럼"] + file_names + ["통합값"]
        )
        log_df = pd.DataFrame(log_rows, columns=["구분", "항목", "내용"])
        return {"통합결과": result_df, "파일별비교": compare_df, "처리로그": log_df}

    @staticmethod
    def describe_df(df: pd.DataFrame, max_rows: int = 5) -> str:
        lines = [
            f"행: {len(df)} / 열: {len(df.columns)}",
            f"컬럼: {', '.join(df.columns.tolist())}",
            "",
            "데이터 타입:",
        ]
        for col, dtype in df.dtypes.items():
            lines.append(f"  {col}: {dtype}")
        lines += ["", "미리보기 (상위 행):"]
        lines.append(df.head(max_rows).to_string(index=False))
        return "\n".join(lines)

    @staticmethod
    def save_excel(df: pd.DataFrame, filename: Optional[str] = None) -> Path:
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"result_{ts}.xlsx"
        path = settings.RESULTS_DIR / filename
        if filename.endswith(".csv"):
            df.to_csv(path, index=False, encoding="utf-8-sig")
        else:
            df.to_excel(path, index=False)
        return path

    @staticmethod
    def save_multi_sheet(
        sheets: dict[str, pd.DataFrame], filename: Optional[str] = None
    ) -> Path:
        """여러 DataFrame을 시트별로 담은 단일 엑셀 파일로 저장한다."""
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"통합결과_{ts}.xlsx"
        path = settings.RESULTS_DIR / filename
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            for sheet_name, df in sheets.items():
                df.to_excel(writer, sheet_name=str(sheet_name)[:31], index=False)
        return path

    @staticmethod
    def save_md(content: str, filename: Optional[str] = None) -> Path:
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"result_{ts}.md"
        path = settings.RESULTS_DIR / filename
        path.write_text(content, encoding="utf-8")
        return path


excel_processor = ExcelProcessor()
