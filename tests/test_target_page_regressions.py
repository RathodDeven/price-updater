from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "extract_price_table.py"


def _extract_target_page(input_pdf: Path, target_page: int, output_xlsx: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input-pdf",
            str(input_pdf),
            "--output-xlsx",
            str(output_xlsx),
            "--target-page",
            str(target_page),
        ],
        cwd=ROOT,
        check=True,
    )


def _read_aliases(xlsx_path: Path) -> list[str]:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    aliases: list[str] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        aliases.append(str(row[0]).strip())
    return aliases


def test_sample_1_page_7_extracts_expected_rows(tmp_path: Path) -> None:
    out_xlsx = tmp_path / "sample_1_p7.xlsx"
    _extract_target_page(ROOT / "samples" / "sample_1.pdf", 7, out_xlsx)
    aliases = _read_aliases(out_xlsx)
    assert len(aliases) == 18
    assert "5SV53120RC" in aliases
    assert "5SV56460RC" in aliases


def test_sample_1_page_8_extracts_expected_rows(tmp_path: Path) -> None:
    out_xlsx = tmp_path / "sample_1_p8.xlsx"
    _extract_target_page(ROOT / "samples" / "sample_1.pdf", 8, out_xlsx)
    aliases = _read_aliases(out_xlsx)
    assert len(aliases) == 30
    assert "5TJ83110" in aliases
    assert "5TJ86460" in aliases


def test_sample_1_page_9_extracts_expected_rows(tmp_path: Path) -> None:
    out_xlsx = tmp_path / "sample_1_p9.xlsx"
    _extract_target_page(ROOT / "samples" / "sample_1.pdf", 9, out_xlsx)
    aliases = _read_aliases(out_xlsx)
    assert len(aliases) == 24
    assert "5TE3125-0RC" in aliases
    assert "5TE3492-0RC" in aliases


def test_sample_1_page_10_extracts_second_table(tmp_path: Path) -> None:
    out_xlsx = tmp_path / "sample_1_p10.xlsx"
    _extract_target_page(ROOT / "samples" / "sample_1.pdf", 10, out_xlsx)
    aliases = _read_aliases(out_xlsx)
    assert len(aliases) == 29
    assert "8GB30101RC04" in aliases
    assert "8GB30202RC12" in aliases


def test_sample_4_page_79_includes_left_column_aliases(tmp_path: Path) -> None:
    out_xlsx = tmp_path / "sample_4_p79.xlsx"
    _extract_target_page(ROOT / "samples" / "sample_4.pdf", 79, out_xlsx)
    aliases = _read_aliases(out_xlsx)
    assert len(aliases) >= 25
    assert "416870" in aliases
    assert "416873" in aliases
