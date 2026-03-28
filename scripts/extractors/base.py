"""Abstract base class for table extraction backends."""

from abc import ABC, abstractmethod
from pathlib import Path


class TableExtractor(ABC):
    """Interface for PDF table extraction backends."""

    @abstractmethod
    def extract_tables(self, pdf_path: Path, candidate_pages: list[int]) -> dict[int, list[list[str]]]:
        """
        Extract tables from candidate pages in a PDF.

        Args:
            pdf_path: Path to the PDF file.
            candidate_pages: List of 0-indexed page numbers to process.

        Returns:
            Dictionary mapping page number to list of table matrices.
            Each matrix is a list of rows, each row is a list of cell strings.
        """
        pass

    @abstractmethod
    def supports_page_triage(self) -> bool:
        """Return True if this backend supports fast page scoring without full extraction."""
        pass
