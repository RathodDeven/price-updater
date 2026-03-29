"""Google Document AI table extraction backend (paid, for scanned/complex layouts)."""

from pathlib import Path

from google.cloud import documentai

from .base import TableExtractor


class DocumentAIExtractor(TableExtractor):
    """Extract tables using Google Document AI (paid, for complex/scanned PDFs).

    Best for:
    - Scanned image PDFs
    - Complex multi-column layouts
    - Enterprise requirements

    Cost: ~$1-3 per page after triage filtering.
    """

    def __init__(self, project_id: str, location: str, processor_id: str, processor_version: str = ""):
        """
        Initialize Document AI extractor.

        Args:
            project_id: Google Cloud project ID.
            location: Document AI processor location (e.g., "us").
            processor_id: Document AI processor ID.
            processor_version: Optional processor version ID (defaults to latest).
        """
        self.project_id = project_id
        self.location = location
        self.processor_id = processor_id
        self.processor_version = processor_version
        self.client = documentai.DocumentProcessorServiceClient()

    def extract_tables(self, pdf_path: Path, candidate_pages: list[int]) -> dict[int, list[list[list[str]]]]:
        """Extract tables from candidate pages using Document AI."""
        if self.processor_version:
            processor_name = self.client.processor_version_path(
                self.project_id, self.location, self.processor_id, self.processor_version
            )
        else:
            processor_name = self.client.processor_path(self.project_id, self.location, self.processor_id)

        result: dict[int, list[list[list[str]]]] = {}

        for page_index in candidate_pages:
            try:
                page_pdf_bytes = self._build_single_page_pdf_bytes(pdf_path, page_index)
                request = documentai.ProcessRequest(
                    name=processor_name,
                    raw_document=documentai.RawDocument(content=page_pdf_bytes, mime_type="application/pdf"),
                )
                response = self.client.process_document(request=request)
                document = response.document

                if document.pages:
                    page_result = []
                    first_page = document.pages[0]
                    for table in first_page.tables:
                        matrix = self._table_to_matrix(table, document.text)
                        page_result.append(matrix)
                    if page_result:
                        result[page_index] = page_result
            except Exception:
                pass

        return result

    @staticmethod
    def _build_single_page_pdf_bytes(pdf_path: Path, page_index: int) -> bytes:
        """Build a single-page PDF as bytes."""
        import fitz

        src = fitz.open(pdf_path)
        single = fitz.open()
        single.insert_pdf(src, from_page=page_index, to_page=page_index)
        data = single.tobytes(garbage=True, deflate=True)
        single.close()
        src.close()
        return data

    @staticmethod
    def _table_to_matrix(table: documentai.Document.Page.Table, full_text: str) -> list[list[str]]:
        """Convert a Document AI table to a string matrix."""
        matrix: list[list[str]] = []
        for row in table.header_rows:
            matrix.append([DocumentAIExtractor._layout_text(cell.layout, full_text) for cell in row.cells])
        for row in table.body_rows:
            matrix.append([DocumentAIExtractor._layout_text(cell.layout, full_text) for cell in row.cells])
        return matrix

    @staticmethod
    def _layout_text(layout: documentai.Document.Page.Layout, full_text: str) -> str:
        """Extract text from a layout object."""
        if not layout.text_anchor.text_segments:
            return ""
        chunks: list[str] = []
        for segment in layout.text_anchor.text_segments:
            start = int(segment.start_index) if segment.start_index else 0
            end = int(segment.end_index)
            chunks.append(full_text[start:end])
        return " ".join("".join(chunks).split())

    def supports_page_triage(self) -> bool:
        """Document AI benefits from page triage to reduce API calls/cost."""
        return True
