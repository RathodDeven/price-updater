import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.alias_price_stream import extract_alias_price_stream_rows


def test_alias_price_stream_extracts_flattened_pairs() -> None:
    matrix = [
        ["DPX 250", "4201 60", "1540", "4201 61", "2230", "4210 12 3000 4210 13 3000", ""],
        ["DPX 1600", "0262 61", "5170", "0262 83", "7930", "-", "4222 39 3190 4222 40 3190"],
    ]

    rows = extract_alias_price_stream_rows(matrix, page_number=42)
    keys = {(r.alias, r.purchase) for r in rows}

    assert ("420160", 1540.0) in keys
    assert ("420161", 2230.0) in keys
    assert ("421012", 3000.0) in keys
    assert ("421013", 3000.0) in keys
    assert ("026261", 5170.0) in keys
    assert ("026283", 7930.0) in keys
    assert ("422239", 3190.0) in keys
    assert ("422240", 3190.0) in keys


def test_alias_price_stream_does_not_cross_cell_boundaries() -> None:
    matrix = [
        ["DPX 250", "4201 60", "1540", "4201 61", "2230", "4210 12 3000 4210 13 3000", ""],
    ]

    rows = extract_alias_price_stream_rows(matrix, page_number=42)
    keys = {(r.alias, r.purchase) for r in rows}
    assert ("15404201", 61.0) not in keys
    assert ("24704201", 63.0) not in keys
    assert ("420160", 1540.0) in keys
    assert ("420161", 2230.0) in keys
    assert ("421012", 3000.0) in keys
    assert ("421013", 3000.0) in keys


def test_alias_price_stream_ignores_pack_only_values() -> None:
    matrix = [
        ["", "0288 48", "1", "-", "-"],
        ["", "0288 49", "1", "-", "-"],
        ["", "0288 50", "1", "-", "-"],
        ["", "0288 51", "1", "-", "-"],
    ]

    rows = extract_alias_price_stream_rows(matrix, page_number=18)
    assert rows == []


def test_alias_price_stream_ignores_leading_zero_code_as_price() -> None:
    matrix = [
        ["DPX 1600", "-", "0261 40 0261 41 - 0261 44 62010 0261 45 62020 0261 46 62030 0261 47 62040"],
    ]

    rows = extract_alias_price_stream_rows(matrix, page_number=42)
    keys = {(r.alias, r.purchase) for r in rows}
    assert ("026140", 261.0) not in keys
    assert ("026144", 62010.0) in keys


def test_alias_price_stream_recovers_trailing_valid_pair_after_alias_only_entries() -> None:
    matrix = [
        ["DPX 1600", "-", "0261 19 0261 28 0261 29 0261 27 72480"],
        ["DPX 1600", "-", "0261 24 0261 25 0261 26 71800 0261 23 71800"],
        ["DPX 630", "-", "0261 40 0261 41 0261 44 62010 0261 45 62020"],
    ]

    rows = extract_alias_price_stream_rows(matrix, page_number=42)
    keys = {(r.alias, r.purchase) for r in rows}
    assert ("026127", 72480.0) in keys
    assert ("026126", 71800.0) in keys
    assert ("026123", 71800.0) in keys
    assert ("026144", 62010.0) in keys
    assert ("026119", 72480.0) not in keys


def test_alias_price_stream_handles_alternating_price_and_alias_lines() -> None:
    matrix = [
        ["", "", "42400\n4210 61\n38080\n4210 61\n38080"],
        ["", "", "0262 73 31950 0262 74 33700"],
        ["", "", "0261 19\n0261 28\n0261 29\n0261 27\n72480"],
        ["", "", "0261 40\n0261 41\n0261 44\n62010"],
    ]

    rows = extract_alias_price_stream_rows(matrix, page_number=42)
    keys = {(r.alias, r.purchase) for r in rows}
    assert ("424004210", 61.0) not in keys
    assert ("380804210", 61.0) not in keys
    assert ("319500262", 74.0) not in keys
    assert ("421061", 38080.0) in keys
    assert ("026273", 31950.0) in keys
    assert ("026274", 33700.0) in keys
    assert ("026127", 72480.0) in keys
    assert ("026144", 62010.0) in keys
