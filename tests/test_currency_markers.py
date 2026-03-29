import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from core.models import ACTIVE_ROLE_SYNONYMS
from core.role_markers import has_role_marker


def test_rupee_symbol_and_inr_mark_purchase_role() -> None:
    ACTIVE_ROLE_SYNONYMS.clear()
    ACTIVE_ROLE_SYNONYMS.update(
        {
            "alias": ["reference no"],
            "purchase": ["mrp", "₹", "inr", "rs"],
            "particulars": ["description"],
            "pack": ["std pkg", "nos"],
        }
    )

    assert has_role_marker("Unit MRP* ₹", "purchase", include_role_name=True) is True
    assert has_role_marker("Price in INR", "purchase", include_role_name=True) is True
    assert has_role_marker("Rate Rs.", "purchase", include_role_name=True) is True
