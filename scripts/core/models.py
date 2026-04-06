"""Data models and constants for price extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class NormalizedRow:
    """Extracted price row with validated fields."""
    particulars: str
    alias: str
    purchase: float
    pack: str
    source_page: int

# Profile roles that must be present in header configuration
REQUIRED_PROFILE_ROLES = {"alias", "purchase", "particulars", "pack"}

# Global role synonyms (loaded from JSON profile at startup)
ACTIVE_ROLE_SYNONYMS: dict[str, list[str]] = {
    "alias": [],
    "purchase": [],
    "particulars": [],
    "pack": [],
}

# Active triage configuration (loaded during startup).
ACTIVE_TRIAGE_ROLE_WEIGHTS: dict[str, int] = {}

ACTIVE_TRIAGE_ROLE_MARKERS: dict[str, list[str]] = {
    "alias": [],
    "purchase": [],
    "particulars": [],
    "pack": [],
}

# Regex patterns for validation
PRICE_PATTERN = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
ALIAS_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}$")
PACK_PATTERN = re.compile(r"^[A-Za-z0-9\-_/\.xX]+$")
PACK_LINE_HINT = re.compile(r"^(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?\s*(?:nos?|pcs?|pc|set|box|pkt|unit|uom))$", re.IGNORECASE)
NON_ALIAS_UNIT_PATTERN = re.compile(r"^\d+(?:\.\d+)?(?:MA|A|P|V|KV|W|KW|MW|HZ|KA)$", re.IGNORECASE)
# Regex pattern for numeric ranges: "1 to 1.6", "2.5 to 4", "1-1.6", "2.5-4", etc.
# These are electrical/technical ratings, not product codes.
NON_ALIAS_RANGE_PATTERN = re.compile(r"^\d+(?:\.\d+)?(?:\s+)?(?:to|[-–—]|//)(?:\s+)?\d+(?:\.\d+)?$", re.IGNORECASE)
