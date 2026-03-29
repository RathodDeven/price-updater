"""Excel export functionality."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from core.models import NormalizedRow


def export_xlsx(
    rows: list[NormalizedRow],
    output_path: Path,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> None:
    """Export normalized rows to Excel workbook.

    Default columns: alias, purchase, source_page.
    Optional columns are enabled by CLI flags.
    """
    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise RuntimeError("Failed to access workbook active sheet")
    ws.title = "extracted_prices"

    headers = ["alias", "purchase"]
    if include_particulars:
        headers.append("particulars")
    if include_pack:
        headers.append("pack")
    headers.append("source_page")
    ws.append(headers)

    for row in rows:
        values: list[object] = [row.alias, row.purchase]
        if include_particulars:
            values.append(row.particulars)
        if include_pack:
            values.append(row.pack)
        values.append(row.source_page)
        ws.append(values)
    wb.save(output_path)
