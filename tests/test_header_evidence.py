import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.header import build_column_mappings, enrich_header_row, has_alias_header_evidence, has_purchase_header_evidence
from core.models import ACTIVE_ROLE_SYNONYMS


@pytest.fixture(autouse=True)
def _seed_role_synonyms() -> None:
    ACTIVE_ROLE_SYNONYMS.clear()
    ACTIVE_ROLE_SYNONYMS.update(
        {
            "alias": ["reference", "ref no", "item code", "catalog no", "cat.nos", "cat nos"],
            "purchase": ["mrp", "unit mrp", "price", "purchase", "rate"],
            "particulars": ["item description", "description"],
            "pack": ["std pkg", "pack", "nos", "uom"],
        }
    )


def test_has_purchase_header_evidence_rejects_rated_current() -> None:
    assert has_purchase_header_evidence("Rated Current In (A)") is False
    assert has_purchase_header_evidence("Unit MRP* R") is True


def test_has_alias_header_evidence_rejects_pack_column() -> None:
    assert has_alias_header_evidence("Std. Pkg.** (Nos.)") is False
    assert has_alias_header_evidence("MW# Reference No") is True


def test_repeated_headers_prefer_mrp_over_rated_current() -> None:
    headers = [
        "Type and frame size",
        "Rated Current (A)",
        "3P Cat.Nos",
        "MRP*/ /Unit",
        "Pack",
        "4P Cat.Nos",
        "MRP*/ /Unit",
        "Pack",
    ]

    mappings = build_column_mappings(headers)

    assert mappings == [
        {"alias": 2, "purchase": 3, "pack": 4},
        {"alias": 5, "purchase": 6, "pack": 7},
    ]


def test_enriched_headers_include_prior_mrp_fragment_for_split_headers() -> None:
    matrix = [
        ["", "", "MRP*", ""],
        ["Cat.Nos", "Description", "` /", "Pack"],
        ["", "", "Unit", ""],
        ["4149 45", "Power Supply Module", "10904", "1"],
    ]

    enriched = enrich_header_row(matrix, 1, matrix[1], lookahead_rows=1)

    assert enriched == ["Cat.Nos", "Description", "MRP* ` / Unit", "Pack"]
    assert build_column_mappings(enriched) == [
        {"alias": 0, "purchase": 2, "particulars": 1, "pack": 3},
    ]
