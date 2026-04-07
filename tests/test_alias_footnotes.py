import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.parsing import extract_alias, looks_like_alias
from core.normalization_helpers import is_strong_alias_candidate


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


def test_extract_alias_strips_flattened_numeric_footnote_suffix() -> None:
    assert extract_alias("4122 761", allow_numeric=True) == "412276"
    assert extract_alias("4122 571", allow_numeric=True) == "412257"


def test_extract_alias_keeps_regular_spaced_numeric_codes() -> None:
    assert extract_alias("4122 270", allow_numeric=True) == "4122270"


def test_extract_alias_preserves_alphanumeric_suffix_after_spaced_numeric_prefix() -> None:
    assert extract_alias("5757 12PL", allow_numeric=True) == "575712PL"
    assert extract_alias("5758 34PL", allow_numeric=True) == "575834PL"


def test_is_strong_alias_candidate_rejects_descriptive_word_number_tokens() -> None:
    assert is_strong_alias_candidate("SOCKET-3", allow_numeric=True) is False
    assert is_strong_alias_candidate("WAY-1", allow_numeric=True) is False
    assert is_strong_alias_candidate("MODULE-2", allow_numeric=True) is False


def test_looks_like_alias_rejects_collapsed_voltage_headings() -> None:
    assert looks_like_alias("DOUBLEPOLE415V") is False
    assert looks_like_alias("FOURPOLE415V") is False
    assert looks_like_alias("5ST38140RC") is True


def test_looks_like_alias_rejects_rated_power_descriptors() -> None:
    assert looks_like_alias("5-300W/75W") is False
    assert looks_like_alias("60TO400W") is False
    assert looks_like_alias("AC24107MB") is True


def test_looks_like_alias_rejects_prefixed_dimension_descriptors() -> None:
    assert looks_like_alias("JB150X150X65-90") is False
    assert looks_like_alias("JBFLY225X225X65-90") is False
    assert looks_like_alias("AC24103MB") is True
