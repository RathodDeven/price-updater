"""Text manipulation and normalization utilities."""

from __future__ import annotations

import re

from core.parsing import looks_like_alias_line, parse_price


def normalize_header(value: str) -> str:
    """Lowercase and remove special characters from header text."""
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def split_cell_lines(value: str) -> list[str]:
    """Split multiline cell content into individual lines."""
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_pack_tokens(value: str) -> list[str]:
    """Split pack cell content into pack tokens or lines.
    
    Handles both multiline cells and space-separated repeated values.
    """
    tokens: list[str] = []
    for line in split_cell_lines(value):
        # Camelot sometimes groups repeated pack values as "1 1 1 1"
        if re.fullmatch(r"\d+(?:\s+\d+)+", line):
            tokens.extend(part.strip() for part in line.split() if part.strip())
        else:
            tokens.append(line)
    return tokens


def is_probable_section_heading(value: str) -> bool:
    """Detect generic section labels (e.g., product family titles).
    
    Used to filter out headers mistaken for particulars.
    """
    text = " ".join(value.split()).strip()
    if not text:
        return False
    if looks_like_alias_line(text):
        return False
    if parse_price(text) is not None:
        return False

    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) < 3:
        return False

    # Pure alphabetic multi-word lines are usually section headers
    if all(re.fullmatch(r"[A-Za-z&()'.-]+", w) for w in words):
        return True
    return False


def extract_alias_entries(value: str) -> list[tuple[str, str]]:
    """Extract alias codes and surrounding context from a multiline cell.
    
    Returns list of (alias, context) tuples where context is nearby text.
    """
    from core.parsing import clean_alias
    
    lines = split_cell_lines(value)
    out: list[tuple[str, str]] = []

    for idx, line in enumerate(lines):
        if not looks_like_alias_line(line):
            continue

        alias = clean_alias(line)
        parts: list[str] = []
        for neighbor_idx in (idx - 1, idx + 1):
            if neighbor_idx < 0 or neighbor_idx >= len(lines):
                continue
            candidate = lines[neighbor_idx].strip()
            if not candidate:
                continue
            if looks_like_alias_line(candidate):
                continue
            if is_probable_section_heading(candidate):
                continue
            if parse_price(candidate) is not None and not re.search(r"[A-Za-z+/]", candidate):
                continue
            if candidate not in parts:
                parts.append(candidate)

        out.append((alias, " / ".join(parts)))

    return out


def fallback_particulars(row: list[str], used_indices: set[int]) -> str:
    """Extract particulars from unused cells in a row.
    
    Selects longest non-price text from columns not already assigned.
    """
    from core.models import PRICE_PATTERN
    
    candidates = [
        cell.strip()
        for idx, cell in enumerate(row)
        if idx not in used_indices and cell and not PRICE_PATTERN.fullmatch(cell.replace(",", ""))
    ]
    if not candidates:
        return ""
    return max(candidates, key=len)
