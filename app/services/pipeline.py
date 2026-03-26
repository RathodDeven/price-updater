from pathlib import Path

import pandas as pd

from app.models.schemas import ProcessingSummary
from app.services.exporter import Exporter
from app.services.google_docai import GoogleDocAIExtractor
from app.services.matcher import Matcher
from app.services.openai_fallback import OpenAIFallbackExtractor
from app.services.pdf_service import PDFService
from app.services.validator import RowValidator


class ProcessingPipeline:
    def __init__(self) -> None:
        self.pdf_service = PDFService()
        self.docai = GoogleDocAIExtractor()
        self.fallback = OpenAIFallbackExtractor()
        self.validator = RowValidator()
        self.matcher = Matcher()
        self.exporter = Exporter()

    def run(self, pdf_path: Path, excel_path: Path, run_dir: Path) -> ProcessingSummary:
        manifest = self.pdf_service.preprocess_pdf(pdf_path, run_dir)
        manifest_df = pd.DataFrame(manifest)

        candidate_rows: list[dict] = []
        accepted_rows: list[dict] = []
        review_rows: list[dict] = []

        for item in manifest:
            page_number = int(item["page_number"])
            image_path = Path(item["image_path"])
            native_text = Path(item["text_path"]).read_text(encoding="utf-8")

            primary = self.docai.extract_page(image_path, page_number, native_text)
            extraction = primary
            if primary.status in {"error", "no_rows"}:
                extraction = self.fallback.extract_page(image_path, page_number, native_text)

            for row in extraction.rows:
                raw = row.model_dump()
                candidate_rows.append(raw)
                ok, reason = self.validator.validate(row)
                normalized = self.validator.normalize_row(row)
                normalized["review_reason"] = reason
                if ok:
                    accepted_rows.append(normalized)
                else:
                    review_rows.append(normalized)

        candidates_df = pd.DataFrame(candidate_rows)
        accepted_df = pd.DataFrame(accepted_rows)
        review_df = pd.DataFrame(review_rows)

        excel_df = self.matcher.load_excel(excel_path)
        matched_df, unmatched_df = self.matcher.match(excel_df, accepted_df if not accepted_df.empty else pd.DataFrame(columns=["normalized_code"]))

        files = self.exporter.export_all(
            run_dir=run_dir,
            manifest_df=manifest_df,
            candidates_df=candidates_df,
            accepted_df=accepted_df,
            review_df=review_df,
            matched_df=matched_df,
            unmatched_df=unmatched_df,
        )

        return ProcessingSummary(
            pdf_filename=pdf_path.name,
            excel_filename=excel_path.name,
            total_pages=len(manifest),
            candidate_rows=len(candidates_df),
            accepted_rows=len(accepted_df),
            review_rows=len(review_df),
            matched_rows=int(matched_df["normalized_code"].notna().sum()) if "normalized_code" in matched_df.columns else 0,
            output_dir=str(run_dir),
            files=files,
        )
