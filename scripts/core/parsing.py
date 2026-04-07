"""Price, alias, and pack parsing utilities."""

from __future__ import annotations

import re

from core.models import (
    ALIAS_PATTERN,
    NON_ALIAS_UNIT_PATTERN,
    NON_ALIAS_RANGE_PATTERN,
    PACK_LINE_HINT,
    PACK_PATTERN,
    PRICE_PATTERN,
)


NON_ALIAS_POLE_PATTERN = re.compile(r"^\d+(?:[\-\s]?(?:P|POLE|POLES))$", re.IGNORECASE)
NON_ALIAS_IP_RATING_PATTERN = re.compile(r"^IP\d{1,2}$", re.IGNORECASE)
NON_ALIAS_IP_IK_PATTERN = re.compile(r"^IP\d{1,3}(?:/IK\d{1,3})?$", re.IGNORECASE)
NON_ALIAS_DIMENSION_PATTERN = re.compile(r"^\d{1,4}X\d{1,4}(?:/\d{1,4})+$", re.IGNORECASE)
NON_ALIAS_PREFIXED_DIMENSION_PATTERN = re.compile(
    r"^[A-Za-z]{1,12}\d{2,4}X\d{2,4}(?:X\d{2,4})+(?:-\d{2,4})?$",
    re.IGNORECASE,
)
NON_ALIAS_COLLAPSED_VOLTAGE_HEADING_PATTERN = re.compile(r"^[A-Za-z]{5,}\d{2,4}(?:V|KV)$", re.IGNORECASE)
NON_ALIAS_RATING_DESCRIPTOR_PATTERN = re.compile(
    r"^\d+(?:\.\d+)?(?:\s*(?:TO|[-–—/])\s*\d+(?:\.\d+)?(?:\s*(?:MA|A|V|KV|W|KW|MW|VA|KVA|HZ))?)+\s*(?:MA|A|V|KV|W|KW|MW|VA|KVA|HZ)?$",
    re.IGNORECASE,
)
TRAILING_ALIAS_FOOTNOTE_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9\-_/\.]*?)([0-9\u00B9\u00B2\u00B3\u2070-\u2079])\)\s*$")
NUMERIC_ALIAS_PATTERN = re.compile(r"^\d{5,12}$")

# Price-on-request markers used in catalogs (■, •, etc.)
PRICE_ON_REQUEST_PATTERN = re.compile(r"[\uf06e\uf0b7\u25a0\u25cf\u2022]")


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
    # Reject cells with price-on-request markers (■, •, etc.)
    if PRICE_ON_REQUEST_PATTERN.search(compact):
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


def normalize_spaced_numeric_alias(numeric_group: str) -> str:
    """Normalize spaced numeric aliases and strip probable flattened footnote digits.

    Some PDFs render superscript footnote markers next to Cat.Nos values
    (for example ``4122 76¹``). Table extraction can flatten this into
    ``4122 761``. For spaced numeric aliases, treat a trailing 1/2/3 in a
    3-digit suffix as a likely footnote marker and drop it.
    """
    parts = re.split(r"[ \t]+", numeric_group.strip())
    if len(parts) != 2:
        return clean_alias(numeric_group)
    left, right = parts[0], parts[1]
    if len(right) == 3 and right[-1] in {"1", "2", "3"}:
        candidate = f"{left}{right[:-1]}"
        if looks_like_alias(candidate, allow_numeric=True):
            return candidate
    return clean_alias(numeric_group)


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
        # Reject pure-letter concatenations from multi-word description text.
        # In mixed layouts, alphabetic-only tokens are usually not product codes.
        if has_mixed_layout and re.fullmatch(r"[A-Za-z\-_/\.]+", candidate):
            # Skip pure-alphabet tokens from multiword context to avoid choosing description.
            continue
        if len(candidate) > len(best):
            best = candidate

    cleaned = clean_alias(base_value)

    if allow_numeric:
        # Recover aliases split by a newline between short prefix and the
        # remaining spaced Cat.No group (e.g. "5\n078 60" -> "507860").
        lines = [line.strip() for line in base_value.splitlines() if line.strip()]
        for idx in range(len(lines) - 1):
            prefix = lines[idx]
            next_line = lines[idx + 1]
            if not re.fullmatch(r"\d{1,2}", prefix):
                continue
            split_match = re.match(
                r"^(\d{2,6})[ \t](\d{2,6})[ \t]+([A-Za-z])\b",
                next_line,
            )
            if not split_match:
                split_match = re.match(
                    r"^(\d{2,6})[ \t](\d{2,6})([A-Za-z]{1,6})?\b",
                    next_line,
                )
            if not split_match:
                continue
            suffix = split_match.group(3) or ""
            base_candidate = clean_alias(f"{split_match.group(1)}{split_match.group(2)}{suffix}")
            # Only rejoin when the second line looks like a truncated tail
            # (leading zero after split). This avoids prepending unrelated
            # one-digit technical values from merged cells.
            if not base_candidate.startswith("0"):
                continue
            candidate = clean_alias(f"{prefix}{base_candidate}")
            if looks_like_alias(candidate, allow_numeric=True):
                return candidate

        # Handle split numeric aliases with one-letter spaced suffixes like
        # "5078 86 N".
        for match in re.finditer(
            r"\b(\d{3,6})[ \t](\d{2,6})[ \t]+([A-Za-z])\b",
            base_value,
        ):
            numeric_candidate = clean_alias(f"{match.group(1)}{match.group(2)}{match.group(3)}")
            if looks_like_alias(numeric_candidate, allow_numeric=True):
                return numeric_candidate

        # Handle attached suffix variants such as "5757 12PL".
        for match in re.finditer(
            r"\b(\d{3,6})[ \t](\d{2,6})([A-Za-z]{1,6})\b",
            base_value,
        ):
            numeric_candidate = clean_alias(f"{match.group(1)}{match.group(2)}{match.group(3)}")
            if looks_like_alias(numeric_candidate, allow_numeric=True):
                return numeric_candidate

        # Handle plain split numeric aliases like "0281 32".
        # Use [ \t] instead of \s to avoid matching across newlines, which
        # would merge unrelated numbers from adjacent lines (e.g. 7000\n4240).
        for numeric_group in re.findall(r"\b\d{3,6}[ \t]\d{2,6}\b", base_value):
            numeric_candidate = normalize_spaced_numeric_alias(numeric_group)
            if looks_like_alias(numeric_candidate, allow_numeric=True):
                return numeric_candidate

    if allow_numeric and has_mixed_layout:
        # In mixed multiline cells, keep one line-level numeric alias and avoid
        # concatenating multiple aliases into a synthetic code.
        # Skip description-like lines (many words) which would produce
        # garbage aliases when concatenated (e.g. "DSX DIN Shorting..." → "DSXDIN...").
        for line in base_value.splitlines():
            if len(line.split()) > 3:
                continue
            line_candidate = clean_alias(line)
            if looks_like_alias(line_candidate, allow_numeric=True):
                return line_candidate

    if allow_numeric and cleaned.isdigit() and looks_like_alias(cleaned, allow_numeric=True):
        return cleaned

    if has_mixed_layout and best:
        return best

    # In mixed layout with no individual token qualifying as alias,
    # do not return the full concatenation — it's likely description text.
    if has_mixed_layout:
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
    # Reject IP protection ratings (IP20, IP54, etc.)
    if NON_ALIAS_IP_RATING_PATTERN.match(value):
        return False
    # Reject IP/IK protection class tokens such as IP66/IK09.
    if NON_ALIAS_IP_IK_PATTERN.match(value):
        return False
    # Reject dimension-ratio descriptors such as 50X80/130/180.
    if NON_ALIAS_DIMENSION_PATTERN.match(value):
        return False
    # Reject prefixed dimension descriptors such as JB150X150X65-90.
    if NON_ALIAS_PREFIXED_DIMENSION_PATTERN.match(value):
        return False
    # Reject collapsed section headings such as DOUBLEPOLE415V where a
    # family label and voltage rating are merged without spaces.
    if NON_ALIAS_COLLAPSED_VOLTAGE_HEADING_PATTERN.match(value):
        return False
    # Reject electrical rating descriptors such as 5-300W/75W.
    if NON_ALIAS_RATING_DESCRIPTOR_PATTERN.match(value):
        return False
    # Reject numeric ranges (e.g., "1 to 1.6", "2.5-4") that are electrical ratings
    if NON_ALIAS_RANGE_PATTERN.match(value):
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
