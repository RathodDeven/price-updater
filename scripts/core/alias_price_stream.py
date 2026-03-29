"""Fallback parser for flattened alias/price token streams."""

from __future__ import annotations

import re

from core.models import NormalizedRow
from core.parsing import clean_pack, looks_like_alias, parse_price
from core.quality_scoring import normalized_row_quality
from core.text_utils import split_cell_lines


NUMERIC_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?")
MIN_PURCHASE = 50.0
MAX_PURCHASE = 500000.0


def _normalize_alias(alias_group: str) -> str:
    return "".join(alias_group.split())


def _looks_like_alias_group(first: str, second: str) -> bool:
    return first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6


def _is_valid_price_token(token: str) -> bool:
    if not NUMERIC_TOKEN_PATTERN.fullmatch(token):
        return False
    if len(token) > 1 and token.startswith("0"):
        return False
    try:
        purchase = float(token)
    except ValueError:
        return False
    return MIN_PURCHASE <= purchase <= MAX_PURCHASE


def _extract_inline_triplets(tokens: list[str]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    idx = 0
    while idx <= len(tokens) - 3:
        first, second, price_token = tokens[idx], tokens[idx + 1], tokens[idx + 2]
        if _looks_like_alias_group(first, second) and _is_valid_price_token(price_token):
            alias = _normalize_alias(f"{first} {second}")
            if looks_like_alias(alias, allow_numeric=True):
                pairs.append((alias, round(float(price_token), 2)))
                idx += 3
                continue
        idx += 1
    return pairs


def _extract_row_pairs(text: str) -> list[tuple[str, float]]:
    lines = [" ".join(line.replace(",", " ").split()) for line in split_cell_lines(text)]
    lines = [line for line in lines if line]
    if not lines:
        return []

    pairs: list[tuple[str, float]] = []
    pending_alias: str | None = None

    for line in lines:
        tokens = line.split()
        if len(tokens) >= 3:
            inline_pairs = _extract_inline_triplets(tokens)
            if inline_pairs:
                pairs.extend(inline_pairs)
                pending_alias = None
                continue

        if len(tokens) == 2 and _looks_like_alias_group(tokens[0], tokens[1]):
            alias = _normalize_alias(line)
            if looks_like_alias(alias, allow_numeric=True):
                pending_alias = alias
            continue

        if len(tokens) == 1 and _is_valid_price_token(tokens[0]) and pending_alias is not None:
            pairs.append((pending_alias, round(float(tokens[0]), 2)))
            pending_alias = None
            continue

        pending_alias = None

    return pairs


def _extract_adjacent_cell_pairs(row_cells: list[str]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    for idx in range(len(row_cells) - 1):
        alias_raw = row_cells[idx]
        price_raw = row_cells[idx + 1]
        alias_tokens = alias_raw.split()
        if len(alias_tokens) != 2:
            continue
        first, second = alias_tokens
        if not _looks_like_alias_group(first, second):
            continue
        alias = _normalize_alias(alias_raw)
        if not looks_like_alias(alias, allow_numeric=True):
            continue
        purchase = parse_price(price_raw)
        if purchase is None or purchase < MIN_PURCHASE or purchase > MAX_PURCHASE:
            continue
        pairs.append((alias, round(purchase, 2)))
    return pairs


def extract_alias_price_stream_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Extract rows from flattened token streams like '4210 12 3000 4210 13 3000'."""
    if not matrix:
        return []

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}

    for row in matrix:
        row_cells = [str(cell).strip() for cell in row if str(cell).strip()]
        if not row_cells:
            continue

        row_pairs: list[tuple[str, float]] = []
        row_pairs.extend(_extract_adjacent_cell_pairs(row_cells))
        for target in row_cells:
            row_pairs.extend(_extract_row_pairs(target))

        if not row_pairs:
            continue

        pack = ""
        if include_pack:
            for cell in row_cells:
                parsed_pack = clean_pack(cell)
                if parsed_pack:
                    pack = parsed_pack
                    break

        for alias, purchase in row_pairs:
            normalized = NormalizedRow(
                particulars="" if not include_particulars else "",
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
    return rows if len(rows) >= 4 else []
