from pathlib import Path

import pandas as pd

from app.utils.normalization import looks_like_code, normalize_code


class Matcher:
    def load_excel(self, excel_path: Path) -> pd.DataFrame:
        return pd.read_excel(excel_path)

    def match(self, excel_df: pd.DataFrame, accepted_rows_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        df = excel_df.copy()
        if "Alias" not in df.columns:
            raise ValueError("Excel must contain an 'Alias' column")

        df["Alias"] = df["Alias"].fillna("").astype(str)
        df["alias_non_empty"] = df["Alias"].str.strip().ne("")
        df["alias_code_like"] = df["Alias"].apply(looks_like_code)
        df["normalized_alias"] = df["Alias"].apply(normalize_code)

        accepted = accepted_rows_df.copy()
        if not accepted.empty:
            accepted = accepted.drop_duplicates(subset=["normalized_code"], keep="first")

        matched = df.merge(
            accepted,
            how="left",
            left_on="normalized_alias",
            right_on="normalized_code",
            suffixes=("_excel", "_extracted"),
        )
        unmatched = matched[(matched["alias_code_like"]) & (matched["normalized_code"].isna())].copy()
        return matched, unmatched
