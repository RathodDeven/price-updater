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

    def __init__(self, flavor: str = "auto"):
        """
        Initialize Camelot extractor.

        Args:
            flavor: "auto" (default, tries both), "lattice" (line-based), or "stream" (space-based)
        """
        self.flavor = flavor

    def extract_tables(self, pdf_path: Path, candidate_pages: list[int]) -> dict[int, list[list[list[str]]]]:
        """Extract tables from candidate pages using Camelot."""
        result: dict[int, list[list[list[str]]]] = {}

        for page_num in candidate_pages:
            page_str = str(page_num + 1)  # Camelot uses 1-indexed pages
            page_result: list[list[list[str]]] = []

            if self.flavor in {"lattice", "stream"}:
                flavors = [self.flavor]
            else:
                flavors = ["lattice"]

            for flavor in flavors:
                try:
                    tables = camelot.read_pdf(str(pdf_path), pages=page_str, flavor=flavor, suppress_stdout=True)
                    if tables:
                        for table in tables:
                            page_result.append(table.data)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Camelot failed on page %s (index=%s, flavor=%s): %s",
                        page_num + 1,
                        page_num,
                        flavor,
                        exc,
                    )
                    logger.debug("Camelot page failure details", exc_info=True)

            # Auto mode fallback for tables that have no ruling lines and collapse
            # into very narrow lattice outputs (commonly 1-3 columns).
            if self.flavor == "auto":
                max_cols = max((max((len(row) for row in matrix), default=0) for matrix in page_result), default=0)
                needs_stream_fallback = not page_result or max_cols <= 3

                if needs_stream_fallback:
                    stream_result: list[list[list[str]]] = []
                    try:
                        # edge_tol=500 merges sub-tables separated by section headers or
                        # image rows so the full page is captured in one pass.
                        tables = camelot.read_pdf(
                            str(pdf_path), pages=page_str, flavor="stream",
                            suppress_stdout=True, edge_tol=500,
                        )
                        if tables:
                            for table in tables:
                                stream_result.append(table.data)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Camelot failed on page %s (index=%s, flavor=%s): %s",
                            page_num + 1,
                            page_num,
                            "stream",
                            exc,
                        )
                        logger.debug("Camelot page failure details", exc_info=True)

                    if stream_result:
                        page_result = stream_result

            if page_result:
                result[page_num] = page_result

        return result

    def supports_page_triage(self) -> bool:
        """Camelot requires processing entire pages, so page triage would not save much."""
        return False
