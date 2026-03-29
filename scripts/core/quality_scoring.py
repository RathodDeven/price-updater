"""Quality scoring for rows and columns."""

from __future__ import annotations

import re

from core.models import PACK_LINE_HINT, PRICE_PATTERN, NormalizedRow
from core.parsing import clean_pack, looks_like_alias_line, parse_price
from core.text_utils import split_cell_lines, split_pack_tokens


def line_alias_count(value: str) -> int:
    """Count number of alias-like lines in a cell."""
    lines = split_cell_lines(value)
    return sum(1 for line in lines if looks_like_alias_line(line))


def line_price_count(value: str) -> int:
    """Count number of price-like lines in a cell."""
    lines = split_cell_lines(value)
    return sum(1 for line in lines if parse_price(line) is not None)


def line_pack_count(value: str) -> int:
    """Count number of pack-like lines in a cell."""
    lines = split_pack_tokens(value)
    count = 0
    for line in lines:
        line = line.strip()
        if PACK_LINE_HINT.match(line):
            count += 1
            continue

        numeric_value = parse_price(line)
        if numeric_value is not None and numeric_value.is_integer() and 0 < numeric_value <= 100:
            count += 1
    return count


def pack_column_quality(value: str) -> int:
    """Score likelihood that a multiline cell represents package quantities.
    
    Slash-form values (e.g., "1/12") are strongly preferred over plain integers
    which may represent other measurements (current, voltage).
    """
    lines = split_pack_tokens(value)
    if not lines:
        return 0

    score = 0
    pack_like = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if looks_like_alias_line(line):
            score -= 4
            continue

        if re.search(r"[A-Za-z]", line) and "/" not in line:
            score -= 2

        if "/" in line:
            score += 5
            pack_like += 1
        if PACK_LINE_HINT.match(line):
            score += 3
            pack_like += 1

        numeric_value = parse_price(line)
        if numeric_value is not None and numeric_value.is_integer() and 0 < numeric_value <= 100:
            if "/" in line:
                pack_like += 1
            else:
                score += 2
                pack_like += 1

    if lines and (pack_like / len(lines)) >= 0.7:
        score += 4

    return score


def pack_value_quality(pack: str) -> int:
    """Score quality of a pack value. Used for deduplication ranking."""
    if not pack:
        return 0
    if looks_like_alias_line(pack):
        return -3
    if "/" in pack:
        return 3
    if re.fullmatch(r"\d+", pack):
        return 2
    if re.fullmatch(r"^[A-Za-z0-9\-_/\.xX]+$", pack):
        return 1
    return 0


def normalized_row_quality(row: NormalizedRow) -> int:
    """Calculate overall quality score for a normalized row.
    
    Used to rank duplicate candidates. Higher score = better quality.
    """
    return pack_value_quality(row.pack) + (1 if row.particulars else 0)


def select_pack_column(alias_idx: int, pack_cols: list[int], row: list[str]) -> int | None:
    """Select best pack column candidate based on proximity and quality.
    
    Prefers columns with better pack quality near the alias column.
    """
    if not pack_cols:
        return None

    def rank(idx: int) -> tuple[int, int, int, int]:
        return (
            pack_column_quality(row[idx]),
            -abs(idx - alias_idx),
            1 if idx > alias_idx else 0,
            idx,
        )

    return max(pack_cols, key=rank)
