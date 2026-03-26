from pathlib import Path

from app.core.config import settings
from app.models.schemas import ExtractedRow, PageExtractionResult


class GoogleDocAIExtractor:
    """Primary extractor placeholder.

    The interface is complete so the developer only has to wire the live API.
    """

    def enabled(self) -> bool:
        return bool(
            settings.google_application_credentials
            and settings.google_cloud_project
            and settings.google_docai_processor_id
        )

    def extract_page(self, image_path: Path, page_number: int, native_text: str) -> PageExtractionResult:
        if not self.enabled():
            return PageExtractionResult(
                page_number=page_number,
                status="error",
                rows=[],
                message="Google Document AI credentials not configured.",
            )

        # TODO for developer:
        # 1. Build a Document AI client here.
        # 2. Send the page image or page bytes to the custom extractor.
        # 3. Map returned entities into ExtractedRow.
        # 4. Return success/no_rows/error.
        #
        # For now, return no rows so the OpenAI fallback can run.
        return PageExtractionResult(
            page_number=page_number,
            status="no_rows",
            rows=[],
            message="Document AI wiring pending.",
        )
