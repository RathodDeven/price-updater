from pathlib import Path

import pandas as pd


class Exporter:
    def export_all(
        self,
        run_dir: Path,
        manifest_df: pd.DataFrame,
        candidates_df: pd.DataFrame,
        accepted_df: pd.DataFrame,
        review_df: pd.DataFrame,
        matched_df: pd.DataFrame,
        unmatched_df: pd.DataFrame,
    ) -> dict[str, str]:
        files: dict[str, str] = {}

        mapping = {
            "page_manifest.csv": manifest_df,
            "all_candidate_rows.csv": candidates_df,
            "accepted_rows.csv": accepted_df,
            "review_rows.csv": review_df,
            "matched_rows.csv": matched_df,
            "unmatched_excel_rows.csv": unmatched_df,
        }

        for filename, df in mapping.items():
            path = run_dir / filename
            df.to_csv(path, index=False)
            files[filename] = str(path)

        workbook_path = run_dir / "review_report.xlsx"
        with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
            manifest_df.to_excel(writer, sheet_name="page_manifest", index=False)
            candidates_df.to_excel(writer, sheet_name="candidates", index=False)
            accepted_df.to_excel(writer, sheet_name="accepted", index=False)
            review_df.to_excel(writer, sheet_name="review", index=False)
            matched_df.to_excel(writer, sheet_name="matched", index=False)
            unmatched_df.to_excel(writer, sheet_name="unmatched_excel", index=False)
        files["review_report.xlsx"] = str(workbook_path)
        return files
