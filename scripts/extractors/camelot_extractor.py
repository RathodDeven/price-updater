"""Camelot-based table extraction backend (free, for native PDF tables)."""

import logging
from pathlib import Path

import camelot

from .base import TableExtractor


logger = logging.getLogger(__name__)


class CamelotExtractor(TableExtractor):
    """Extract tables using Camelot (free, works on native/searchable PDFs).

    Best for:
    - PDFs with clear table structure
    - Native (non-scanned) PDFs
    - Zero cost extraction

    Limitations:
    - Struggles with scanned/image PDFs
    - May miss complex multi-column layouts
    """

    def __init__(self, flavor: str = "lattice"):
        """
        Initialize Camelot extractor.

        Args:
            flavor: "lattice" (default, for line-based tables) or "stream" (for space-based)
        """
        self.flavor = flavor

    def extract_tables(self, pdf_path: Path, candidate_pages: list[int]) -> dict[int, list[list[str]]]:
        """Extract tables from candidate pages using Camelot."""
        result: dict[int, list[list[str]]] = {}

        for page_num in candidate_pages:
            page_str = str(page_num + 1)  # Camelot uses 1-indexed pages
            try:
                tables = camelot.read_pdf(str(pdf_path), pages=page_str, flavor=self.flavor, suppress_stdout=True)
                if tables:
                    page_result = []
                    for table in tables:
                        matrix = table.data
                        page_result.append(matrix)
                    if page_result:
                        result[page_num] = page_result
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Camelot failed on page %s (index=%s, flavor=%s): %s",
                    page_num + 1,
                    page_num,
                    self.flavor,
                    exc,
                )
                logger.debug("Camelot page failure details", exc_info=True)

        return result

    def supports_page_triage(self) -> bool:
        """Camelot requires processing entire pages, so page triage would not save much."""
        return False
