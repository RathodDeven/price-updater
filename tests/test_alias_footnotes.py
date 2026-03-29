import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.parsing import extract_alias


def test_extract_alias_strips_trailing_digit_footnote_marker() -> None:
    assert extract_alias("5ST30101)") == "5ST3010"
    assert extract_alias("5ST30311)") == "5ST3031"


def test_extract_alias_keeps_codes_without_footnote_marker() -> None:
    assert extract_alias("5ST38051") == "5ST38051"
    assert extract_alias("5ST38140RC") == "5ST38140RC"


def test_extract_alias_accepts_spaced_numeric_catalog_numbers_when_allowed() -> None:
    assert extract_alias("4149 45", allow_numeric=True) == "414945"


def test_extract_alias_does_not_concatenate_multiline_numeric_aliases() -> None:
    assert extract_alias("0281 32\n0281 34", allow_numeric=True) == "028132"
