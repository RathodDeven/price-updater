"""Fallback parsing for dense merged-column table layouts."""

from __future__ import annotations

import re

from core.header import has_purchase_header_evidence
from core.models import NormalizedRow
from core.parsing import clean_pack, extract_alias, looks_like_alias, parse_price
from core.quality_scoring import normalized_row_quality
from core.text_utils import split_cell_lines


ALIAS_GROUP_PATTERN = re.compile(r"\d{3,6}[ \t]\d{2,6}")
NUMBER_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9])\d{2,7}(?:,\d{3})*(?:\.\d+)?(?![A-Za-z0-9])")


def _cell(row: list[str], idx: int) -> str:
    if idx < 0 or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def _find_data_column(matrix: list[list[str]]) -> int | None:
    max_cols = max((len(row) for row in matrix), default=0)
    if max_cols == 0:
        return None

    best_col = None
    best_hits = 0
    for col in range(max_cols):
        hits = 0
        for row in matrix:
            value = _cell(row, col)
            if value and ALIAS_GROUP_PATTERN.search(value):
                hits += 1
        if hits > best_hits:
            best_hits = hits
            best_col = col

    return best_col if best_hits >= 3 else None


def _find_pack_column(matrix: list[list[str]], data_col: int) -> int | None:
    max_cols = max((len(row) for row in matrix), default=0)
    best_col = None
    best_hits = 0

    for col in range(max_cols):
        if col == data_col:
            continue
        hits = 0
        for row in matrix:
            if clean_pack(_cell(row, col)):
                hits += 1
        if hits > best_hits or (hits == best_hits and best_col is not None and abs(col - data_col) < abs(best_col - data_col)):
            best_hits = hits
            best_col = col

    return best_col if best_hits >= 3 else None


# Price-on-request markers used in catalogs (■, bullet, etc.).
PRICE_ON_REQUEST_CHARS = re.compile(r"[\uf06e\uf0b7\u25a0\u25cf\u2022]")
MIN_PURCHASE = 50.0


def _price_from_dense_cell(text: str) -> float | None:
    # If cell contains a price-on-request marker, MRP is unavailable.
    if PRICE_ON_REQUEST_CHARS.search(text):
        return None
    lines = split_cell_lines(text)
    line_prices = [
        parse_price(line)
        for line in lines
        if not ALIAS_GROUP_PATTERN.search(line)
    ]
    line_prices = [value for value in line_prices if value is not None]
    if line_prices:
        # When Cat.No/MRP/Pack are stacked, the last value is often Pack (1).
        # Prefer the first price above pack range.
        qualified = [v for v in line_prices if v >= MIN_PURCHASE]
        if qualified:
            return round(qualified[0], 2)
        # All prices are below MIN_PURCHASE → likely pack values, not MRP.
        return None

    # Remove alias groups first so alias fragments do not become purchase values.
    scrubbed = ALIAS_GROUP_PATTERN.sub(" ", text)

    # In dense cells with product-description words,
    # plain token fallback can misread model numbers as purchase. Only allow
    # this path when purchase markers are present in the same cell.
    if re.search(r"[A-Za-z]", scrubbed) and not has_purchase_header_evidence(scrubbed):
        return None

    tokens = [match.group(0).replace(",", "") for match in NUMBER_TOKEN_PATTERN.finditer(scrubbed)]
    if not tokens:
        return None

    values: list[float] = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError:
            continue
        if 50 <= value <= 500000:
            values.append(value)

    if not values:
        return None
    return round(values[-1], 2)


def extract_dense_column_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Extract rows when alias and purchase are packed into one dense column.

    This handles layouts where Camelot collapses the table body into one text
    column containing stacked product details, while pack values may remain in
    a neighboring compact column.
    """
    if not matrix:
        return []

    data_col = _find_data_column(matrix)
    if data_col is None:
        return []

    pack_col = _find_pack_column(matrix, data_col) if include_pack else None

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in matrix:
        raw = _cell(row, data_col)
        if not raw or not ALIAS_GROUP_PATTERN.search(raw):
            continue

        # Dense parser expects roughly one alias group per row. When multiple
        # alias groups appear in one cell, alias-price stream fallback is a
        # better fit and avoids pairing the wrong price/pack token.
        if len(ALIAS_GROUP_PATTERN.findall(raw)) >= 2:
            continue

        alias = extract_alias(raw, allow_numeric=True)
        if not looks_like_alias(alias, allow_numeric=True):
            continue

        purchase = _price_from_dense_cell(raw)
        if purchase is None:
            continue

        pack = clean_pack(_cell(row, pack_col)) if include_pack and pack_col is not None else ""
        particulars = "" if not include_particulars else " ".join(
            part for idx, part in enumerate(row) if idx != data_col and idx != pack_col and part.strip()
        ).strip()

        normalized = NormalizedRow(
            particulars=particulars,
            alias=alias,
            purchase=purchase,
            pack=pack,
            source_page=page_number,
        )
        key = (normalized.alias, normalized.purchase)
        existing = best_by_key.get(key)
        if existing is None or normalized_row_quality(normalized) > normalized_row_quality(existing):
            best_by_key[key] = normalized

    rows = list(best_by_key.values())
    return rows if len(rows) >= 3 else []
