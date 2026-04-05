"""Shared helper utilities for normalization logic."""

from __future__ import annotations

import re

from core.parsing import clean_pack, extract_alias, looks_like_alias, parse_price
from core.text_utils import split_cell_lines


CURRENT_LIKE_PURCHASES = {
    0.1,
    0.16,
    0.25,
    0.4,
    0.63,
    1.0,
    1.6,
    2.5,
    4.0,
    5.0,
    6.0,
    7.0,
    9.0,
    10.0,
    12.0,
    16.0,
    18.0,
    20.0,
    22.0,
    25.0,
    28.0,
    32.0,
    40.0,
    50.0,
    54.0,
    60.0,
    63.0,
    65.0,
    70.0,
    80.0,
    85.0,
    100.0,
    120.0,
    125.0,
    160.0,
    185.0,
    200.0,
    240.0,
    250.0,
    260.0,
    300.0,
    320.0,
    330.0,
    400.0,
    520.0,
    630.0,
    800.0,
    1000.0,
    1250.0,
    1600.0,
}


def is_current_like_purchase(value: float) -> bool:
    return round(float(value), 2) in CURRENT_LIKE_PURCHASES


def looks_like_pack_token(text: str) -> bool:
    compact = " ".join(text.split()).strip()
    if not compact:
        return False
    if re.fullmatch(r"\d{1,2}", compact):
        return True
    if re.fullmatch(r"\d{1,2}\s*/\s*\d{1,2}", compact):
        return True
    return False


def extract_last_numeric_price(text: str) -> float | None:
    candidates: list[float] = []
    for token in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text):
        parsed = parse_price(token)
        if parsed is not None:
            candidates.append(parsed)
    if not candidates:
        return None
    return round(candidates[-1], 2)


def extract_trailing_text_price(text: str) -> float | None:
    compact = " ".join(text.split()).strip()
    if not compact:
        return None
    # Split Cat.No groups like "4118 97" are aliases, not prices.
    if re.fullmatch(r"\d{3,6}\s+\d{2,6}", compact):
        return None
    match = re.search(r"(?:^|[^A-Za-z0-9])(\d+(?:,\d{3})*(?:\.\d+)?)\s*$", compact)
    if not match:
        return None
    parsed = parse_price(match.group(1))
    if parsed is None or parsed < 50:
        return None
    return round(parsed, 2)


def extract_inline_price_and_pack(text: str) -> tuple[float | None, str]:
    """Extract leading MRP and trailing pack when both are in one cell.

    Examples:
    - "2220 1/10/100" -> (2220.0, "1/10/100")
    - "1284 1/10/100" -> (1284.0, "1/10/100")
    - "Bell Call ... 1736 1/20/200" -> (1736.0, "1/20/200")
    """
    for line in split_cell_lines(text):
        compact = " ".join(line.split()).strip()
        if not compact:
            continue
        match = re.search(
            r"(?<!\d)(\d+(?:,\d{3})*(?:\.\d+)?)\s+(\d{1,2}\s*/\s*\d{1,3}(?:\s*/\s*\d{1,3})?)\s*$",
            compact,
        )
        if not match:
            continue
        parsed_price = parse_price(match.group(1))
        if parsed_price is None or parsed_price < 50:
            continue
        return round(parsed_price, 2), clean_pack(match.group(2))
    return None, ""


def inline_alias_price_pair(text: str, looks_like_alias_fn) -> tuple[str, float] | None:
    """Extract an inline alias+price pair from one mixed cell when present."""
    best_pair: tuple[str, float] | None = None
    for line in split_cell_lines(text):
        tokens = line.replace(",", " ").split()
        if len(tokens) < 3:
            continue
        for idx in range(len(tokens) - 2):
            first, second, price_token = tokens[idx], tokens[idx + 1], tokens[idx + 2]
            if not (first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6):
                continue
            parsed_price = parse_price(price_token)
            if parsed_price is None:
                continue
            if parsed_price < 50 or parsed_price > 500000:
                continue
            inline_alias = f"{first}{second}"
            if not looks_like_alias_fn(inline_alias, allow_numeric=True):
                continue
            best_pair = (inline_alias, round(parsed_price, 2))
    return best_pair


def mixed_text_alias_price_pair(text: str, looks_like_alias_fn) -> tuple[str, float] | None:
    """Extract one alias+price pair from mixed descriptive text."""
    for line in split_cell_lines(text):
        tokens = line.replace(",", " ").split()
        if len(tokens) < 3:
            continue
        for idx in range(len(tokens) - 1):
            first, second = tokens[idx], tokens[idx + 1]
            if not (first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6):
                continue
            alias_candidate = f"{first}{second}"
            if not looks_like_alias_fn(alias_candidate, allow_numeric=True):
                continue
            price_candidates: list[float] = []
            for token in tokens[idx + 2 :]:
                parsed = parse_price(token)
                if parsed is None:
                    continue
                if 50 <= parsed <= 500000:
                    price_candidates.append(parsed)
            if price_candidates:
                non_current = [v for v in price_candidates if not is_current_like_purchase(v)]
                chosen = max(non_current) if non_current else max(price_candidates)
                return alias_candidate, round(chosen, 2)
    return None


def alias_group_from_text(text: str, looks_like_alias_fn) -> str | None:
    """Extract one numeric alias group from mixed text when present."""
    for line in split_cell_lines(text):
        tokens = line.replace(",", " ").split()
        if len(tokens) < 2:
            continue
        for idx in range(len(tokens) - 1):
            first, second = tokens[idx], tokens[idx + 1]
            if not (first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6):
                continue
            alias_candidate = f"{first}{second}"
            if looks_like_alias_fn(alias_candidate, allow_numeric=True):
                return alias_candidate
    return None


def leading_alias_from_text(text: str, allow_numeric: bool = False) -> str | None:
    """Extract a leading alias token/group from mixed descriptive text."""
    for line in split_cell_lines(text):
        stripped = line.strip()
        if not stripped:
            continue

        alnum_split_match = re.match(r"^(\d{3,6}[ \t]\d{2,6}[A-Za-z]{1,6})\b", stripped)
        if alnum_split_match and allow_numeric:
            candidate = extract_alias(alnum_split_match.group(1), allow_numeric=True)
            if looks_like_alias(candidate, allow_numeric=True):
                return candidate

        numeric_match = re.match(r"^(\d{3,6}[ \t]\d{2,6})\b", stripped)
        if numeric_match and allow_numeric:
            candidate = extract_alias(numeric_match.group(1), allow_numeric=True)
            if looks_like_alias(candidate, allow_numeric=True):
                return candidate

        token_match = re.match(r"^([A-Za-z0-9][A-Za-z0-9\-_/\.]{2,})\b", stripped)
        if not token_match:
            continue
        candidate = extract_alias(token_match.group(1), allow_numeric=allow_numeric)
        if looks_like_alias(candidate, allow_numeric=allow_numeric):
            return candidate
    return None


def is_strong_alias_candidate(alias: str, allow_numeric: bool = False) -> bool:
    """Return True for alias shapes strong enough to override mapped drift."""
    if not looks_like_alias(alias, allow_numeric=allow_numeric):
        return False
    # Descriptive tokens like SOCKET-3 / WAY-1 / MODULE-2 are not catalog
    # aliases even though they are alphanumeric with a hyphen.
    if re.fullmatch(r"[A-Z]{3,}(?:-[A-Z]{2,})*-\d{1,2}", alias, flags=re.IGNORECASE):
        return False
    if alias.isdigit():
        return allow_numeric and len(alias) >= 5

    # Reject electrical ratings/ranges such as 6/16A or 1.5-2.5KV.
    if re.fullmatch(r"\d+(?:[./-]\d+)+(?:[A-Za-z]{1,3})?", alias, flags=re.IGNORECASE):
        return False
    if re.fullmatch(r"\d+(?:\.\d+)?[A-Za-z]{1,3}(?:[/-]\d+(?:\.\d+)?[A-Za-z]{1,3})+", alias, flags=re.IGNORECASE):
        return False

    digit_count = sum(ch.isdigit() for ch in alias)
    return digit_count >= 3 or len(alias) >= 6
