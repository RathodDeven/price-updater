"""Excel export functionality."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from core.models import NormalizedRow


def export_xlsx(rows: list[NormalizedRow], output_path: Path) -> None:
    """Export normalized rows to Excel workbook.
    
    Columns: particulars, alias, purchase, pack, source_page
    """
    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise RuntimeError("Failed to access workbook active sheet")
    ws.title = "extracted_prices"
    ws.append(["particulars", "alias", "purchase", "pack", "source_page"])
    for row in rows:
        ws.append([row.particulars, row.alias, row.purchase, row.pack, row.source_page])
    wb.save(output_path)
