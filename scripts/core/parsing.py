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


NON_ALIAS_POLE_PATTERN = re.compile(r"^\d+(?:[\-\s]?(?:P|POLE|POLES))$", re.IGNORECASE)
TRAILING_ALIAS_FOOTNOTE_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9\-_/\.]*?)([0-9\u00B9\u00B2\u00B3\u2070-\u2079])\)\s*$")
NUMERIC_ALIAS_PATTERN = re.compile(r"^\d{5,12}$")


def parse_price(value: str) -> float | None:
    """Parse price strings with optional thousands separators.
    
    Accepts formats like "139.-", "1,234.50", "999", "1,000,000.99".
    Rejects strings with letters (unit indicators).
    """
    if not value:
        return None
    raw = str(value).strip()
    compact = raw.replace(" ", "")
    if re.search(r"[A-Za-z]", compact):
        return None

    # Reject cells that contain multiple standalone numeric chunks (typically
    # alias+price streams), except canonical spaced-thousands formatting.
    number_chunks = re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", raw)
    if len(number_chunks) > 1:
        normalized_space_group = " ".join(raw.replace(",", "").split())
        if re.fullmatch(r"\d{1,3}(?: \d{3})+(?:\.\d+)?", normalized_space_group):
            compact = normalized_space_group.replace(" ", "")
        else:
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


def strip_alias_footnote_suffix(value: str) -> str:
    """Remove one trailing footnote marker from alias-like strings.

    Some catalogs render reference codes like ``5ST3010<fn1>)``. PDF extraction may
    flatten this to ``5ST30101)``. We strip only the final marker character
    before ')' and keep the main code unchanged.
    """
    raw = value.strip()
    m = TRAILING_ALIAS_FOOTNOTE_PATTERN.match(raw)
    if not m:
        return raw
    return m.group(1)


def extract_alias(value: str, allow_numeric: bool = False) -> str:
    """Extract the best alias token from a raw cell value.

    Handles mixed cells such as multiline values where a small numeric/config
    token is stacked above the real reference number (for example `2\n5SU...`).
    """
    base_value = strip_alias_footnote_suffix(value)
    best = ""
    has_mixed_layout = bool(re.search(r"\s", base_value)) or "\n" in base_value or "\t" in base_value

    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}", base_value.upper()):
        candidate = clean_alias(token)
        if not looks_like_alias(candidate, allow_numeric=allow_numeric):
            continue
        if len(candidate) > len(best):
            best = candidate

    cleaned = clean_alias(base_value)

    if allow_numeric:
        # Handle split numeric aliases like "0281 32" (and multiline variants)
        # without concatenating neighboring aliases from the same cell.
        for numeric_group in re.findall(r"\d{3,6}\s\d{2,6}", base_value):
            numeric_candidate = clean_alias(numeric_group)
            if looks_like_alias(numeric_candidate, allow_numeric=True):
                return numeric_candidate

    if allow_numeric and has_mixed_layout:
        # In mixed multiline cells, keep one line-level numeric alias and avoid
        # concatenating multiple aliases into a synthetic code.
        for line in base_value.splitlines():
            line_candidate = clean_alias(line)
            if looks_like_alias(line_candidate, allow_numeric=True):
                return line_candidate

    if allow_numeric and cleaned.isdigit() and looks_like_alias(cleaned, allow_numeric=True):
        return cleaned

    if has_mixed_layout and best:
        return best

    if looks_like_alias(cleaned, allow_numeric=allow_numeric):
        return cleaned
    return best


def clean_pack(value: str) -> str:
    """Validate and clean pack value. Returns empty string if invalid."""
    v = " ".join(value.split()).strip()
    if not v:
        return ""
    if PACK_PATTERN.match(v):
        return v
    return ""


def looks_like_alias(value: str, allow_numeric: bool = False) -> bool:
    """Check if value matches alias pattern (alphanumeric, 3+ chars)."""
    if not value:
        return False
    # Reject unit values (e.g., "10MA" for current ratings)
    if NON_ALIAS_UNIT_PATTERN.match(value):
        return False
    if NON_ALIAS_POLE_PATTERN.match(value):
        return False
    if allow_numeric and NUMERIC_ALIAS_PATTERN.match(value):
        return True
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
