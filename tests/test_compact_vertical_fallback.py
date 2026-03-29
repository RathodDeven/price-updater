import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.normalization import normalize_rows
from core.models import ACTIVE_ROLE_SYNONYMS
from core.parsing import parse_price


def test_parse_price_rejects_multi_number_streams() -> None:
    assert parse_price("4222 39 3190 4222 40 3190") is None
    assert parse_price("15 670") == 15670.0


def test_compact_vertical_fallback_extracts_rows() -> None:
    ACTIVE_ROLE_SYNONYMS.clear()
    ACTIVE_ROLE_SYNONYMS.update(
        {
            "alias": ["cat.nos", "reference", "item code"],
            "purchase": ["mrp", "unit", "unit price", "price"],
            "particulars": ["description", "type"],
            "pack": ["pack", "nos", "std pkg"],
        }
    )

    matrix = [
        ["Category", "", "Type", "Current", "", "", ""],
        ["", "", "", "(A)", "Cat.Nos", "", "MRP / Unit\nPack"],
        ["", "", "", "16", "4207 10", "", "15670\n1\n-\n-\n-"],
        ["", "", "", "25", "4207 11", "", "15670\n1\n-\n-\n-"],
        ["", "", "", "50", "4207 12", "", "15670\n1\n-\n-\n-"],
        ["", "", "", "63", "4207 13", "", "15670\n1\n-\n-\n-"],
    ]

    rows = normalize_rows(matrix, page_number=42, include_particulars=False, include_pack=True)
    keys = {(r.alias, r.purchase, r.pack) for r in rows}

    assert ("420710", 15670.0, "1") in keys
    assert ("420711", 15670.0, "1") in keys
    assert ("420712", 15670.0, "1") in keys
    assert ("420713", 15670.0, "1") in keys


def test_compact_vertical_fallback_ignores_pack_only_rows() -> None:
    ACTIVE_ROLE_SYNONYMS.clear()
    ACTIVE_ROLE_SYNONYMS.update(
        {
            "alias": ["cat.nos", "reference", "item code"],
            "purchase": ["mrp", "unit", "unit price", "price"],
            "particulars": ["description", "type"],
            "pack": ["pack", "nos", "std pkg"],
        }
    )

    matrix = [
        ["", "Cat.Nos", "Pack"],
        ["", "0288 48", "1"],
        ["", "0288 49", "1"],
        ["", "0288 50", "1"],
        ["", "0288 51", "1"],
    ]

    rows = normalize_rows(matrix, page_number=18, include_particulars=False, include_pack=True)
    assert rows == []


def test_compact_vertical_does_not_cross_pair_with_stream_cell() -> None:
    ACTIVE_ROLE_SYNONYMS.clear()
    ACTIVE_ROLE_SYNONYMS.update(
        {
            "alias": ["cat.nos", "reference", "item code"],
            "purchase": ["mrp", "unit", "unit price", "price"],
            "particulars": ["description", "type"],
            "pack": ["pack", "nos", "std pkg"],
        }
    )

    matrix = [
        ["", "", "", "Cat.Nos", "", "MRP / Unit\nPack"],
        ["", "", "", "6250 18 1150", "", "4210 60\n42400\n4210 61\n38080"],
        ["", "", "", "6250 06 1150", "", "-"],
        ["", "", "", "6250 02 850", "", "-"],
        ["", "", "", "6250 14 850", "", "-"],
    ]

    rows = normalize_rows(matrix, page_number=42, include_particulars=False, include_pack=False)
    keys = {(r.alias, r.purchase) for r in rows}
    assert ("625018", 42400.0) not in keys
    assert ("625018", 1150.0) in keys
