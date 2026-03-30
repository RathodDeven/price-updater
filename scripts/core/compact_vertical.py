"""Fallback parsing for compact vertical tables with stacked price/pack cells."""

from __future__ import annotations

import re

from core.models import NormalizedRow
from core.parsing import clean_pack, extract_alias, looks_like_alias, parse_price
from core.quality_scoring import normalized_row_quality
from core.header import has_purchase_header_evidence
from core.text_utils import split_cell_lines


ALIAS_GROUP_PATTERN = re.compile(r"\d{4,6}\s\d{2,6}")
MIN_COMPACT_PURCHASE = 50.0


def _cell(row: list[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _find_alias_column(matrix: list[list[str]]) -> int | None:
    max_cols = max((len(row) for row in matrix), default=0)
    best_col = None
    best_hits = 0

    for col in range(max_cols):
        hits = 0
        for row in matrix:
            if ALIAS_GROUP_PATTERN.search(_cell(row, col)):
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_col = col

    return best_col if best_hits >= 4 else None


def _purchase_and_pack_from_cell(text: str) -> tuple[float | None, str]:
    # If the candidate price cell already contains alias-like groups, this is
    # usually a flattened stream block and should be handled by stream parser.
    if ALIAS_GROUP_PATTERN.search(text):
        return None, ""

    lines = split_cell_lines(text)
    purchase = None
    pack = ""

    for line in lines:
        if purchase is None:
            parsed = parse_price(line)
            if parsed is not None:
                purchase = round(parsed, 2)
                continue

        if not pack:
            cleaned_pack = clean_pack(line)
            if cleaned_pack:
                pack = cleaned_pack

        if purchase is not None and pack:
            break

    return purchase, pack


def _detect_purchase_evidence_columns(matrix: list[list[str]], scan_rows: int = 12) -> set[int]:
    """Collect columns that explicitly advertise purchase-role headers."""
    purchase_cols: set[int] = set()
    if not matrix:
        return purchase_cols

    max_cols = max((len(row) for row in matrix), default=0)
    limit = min(len(matrix), max(1, scan_rows))
    for row_idx in range(limit):
        row = matrix[row_idx]
        for col in range(max_cols):
            cell = _cell(row, col)
            if not cell:
                continue
            if has_purchase_header_evidence(cell):
                purchase_cols.add(col)
    return purchase_cols


def _find_price_pack_column(
    matrix: list[list[str]],
    alias_col: int,
    allowed_purchase_cols: set[int],
) -> int | None:
    max_cols = max((len(row) for row in matrix), default=0)
    best_col = None
    best_score = 0

    for col in range(max_cols):
        if col == alias_col:
            continue
        if col not in allowed_purchase_cols:
            continue
        score = 0
        for row in matrix:
            alias_raw = _cell(row, alias_col)
            price_pack_raw = _cell(row, col)
            if not alias_raw or not price_pack_raw:
                continue
            if not ALIAS_GROUP_PATTERN.search(alias_raw):
                continue
            purchase, pack = _purchase_and_pack_from_cell(price_pack_raw)
            if purchase is not None:
                score += 1
                lines = split_cell_lines(price_pack_raw)
                if len(lines) >= 2:
                    score += 1
                if pack:
                    score += 2

        if score > best_score or (score == best_score and best_col is not None and abs(col - alias_col) < abs(best_col - alias_col)):
            best_score = score
            best_col = col

    return best_col if best_score >= 8 else None


def extract_compact_vertical_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Extract compact vertical rows where price+pack are stacked in one column."""
    if not matrix:
        return []

    alias_col = _find_alias_column(matrix)
    if alias_col is None:
        return []

    purchase_evidence_cols = _detect_purchase_evidence_columns(matrix)
    if not purchase_evidence_cols:
        return []

    price_pack_col = _find_price_pack_column(matrix, alias_col, purchase_evidence_cols)
    if price_pack_col is None:
        return []

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in matrix:
        alias_raw = _cell(row, alias_col)
        if not ALIAS_GROUP_PATTERN.search(alias_raw):
            continue

        alias = extract_alias(alias_raw, allow_numeric=True)
        if not looks_like_alias(alias, allow_numeric=True):
            continue

        purchase, pack = _purchase_and_pack_from_cell(_cell(row, price_pack_col))
        if purchase is None or purchase < MIN_COMPACT_PURCHASE:
            continue

        normalized = NormalizedRow(
            particulars="" if not include_particulars else "",
            alias=alias,
            purchase=purchase,
            pack=pack if include_pack else "",
            source_page=page_number,
        )
        key = (normalized.alias, normalized.purchase)
        existing = best_by_key.get(key)
        if existing is None or normalized_row_quality(normalized) > normalized_row_quality(existing):
            best_by_key[key] = normalized

    rows = list(best_by_key.values())
    return rows if len(rows) >= 4 else []
