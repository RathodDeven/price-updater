"""Price, alias, and pack parsing utilities."""

from __future__ import annotations

import re

from core.models import (
    ALIAS_PATTERN,
    NON_ALIAS_UNIT_PATTERN,
    PACK_LINE_HINT,
    PACK_PATTERN,
    PRICE_PATTERN,
)


def parse_price(value: str) -> float | None:
    """Parse price strings with optional thousands separators.
    
    Accepts formats like "139.-", "1,234.50", "999", "1,000,000.99".
    Rejects strings with letters (unit indicators).
    """
    if not value:
        return None
    compact = value.replace(" ", "")
    if re.search(r"[A-Za-z]", compact):
        return None
    normalized = compact.replace(",,", ",")
    if normalized.endswith(".-"):
        normalized = normalized[:-2]
    normalized = normalized.strip(".")
    if not normalized or not PRICE_PATTERN.fullmatch(normalized):
        return None
    try:
        return float(normalized.replace(",", ""))
    except ValueError:
        return None


def clean_alias(value: str) -> str:
    """Normalize alias to uppercase, strip whitespace."""
    value = value.strip().upper()
    value = re.sub(r"\s+", "", value)
    return value


def clean_pack(value: str) -> str:
    """Validate and clean pack value. Returns empty string if invalid."""
    v = " ".join(value.split()).strip()
    if not v:
        return ""
    if PACK_PATTERN.match(v):
        return v
    return ""


def looks_like_alias(value: str) -> bool:
    """Check if value matches alias pattern (alphanumeric, 3+ chars)."""
    if not value:
        return False
    # Reject unit values (e.g., "10MA" for current ratings)
    if NON_ALIAS_UNIT_PATTERN.match(value):
        return False
    return bool(ALIAS_PATTERN.match(value))


def looks_like_alias_line(value: str) -> bool:
    """Check if a line (single token) looks like an alias code.
    
    Returns False if it contains spaces (description lines).
    """
    raw = value.strip()
    if not raw:
        return False
    # Product codes are usually single tokens
    if re.search(r"\s", raw):
        return False
    return looks_like_alias(clean_alias(raw))
