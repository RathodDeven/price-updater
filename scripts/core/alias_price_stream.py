"""Fallback parser for flattened alias/price token streams."""

from __future__ import annotations

import re

from core.models import NormalizedRow
from core.parsing import clean_pack, looks_like_alias, normalize_spaced_numeric_alias, parse_price
from core.quality_scoring import normalized_row_quality
from core.text_utils import split_cell_lines


NUMERIC_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?")
ALIAS_GROUP_PATTERN = re.compile(r"\b\d{3,6}[ \t]\d{2,6}\b")
MIN_PURCHASE = 50.0
MAX_PURCHASE = 500000.0
# Price-on-request markers used in catalogs (■, •, etc.)
PRICE_ON_REQUEST_CHARS = re.compile(r"[\uf06e\uf0b7\u25a0\u25cf\u2022]")
UNAVAILABLE_PRICE_MARKER_PATTERN = re.compile(r"^(?:n/?a|na|n|nil|--|-|—|–|\.)$", re.IGNORECASE)


def _normalize_alias(alias_group: str) -> str:
    return normalize_spaced_numeric_alias(alias_group)


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


def _extract_tail_alias_from_cell(text: str) -> str | None:
    lines = split_cell_lines(text)
    for line in reversed(lines):
        alias_groups = ALIAS_GROUP_PATTERN.findall(line)
        if not alias_groups:
            continue
        alias = _normalize_alias(alias_groups[-1])
        if looks_like_alias(alias, allow_numeric=True):
            return alias
    return None


def _extract_leading_price_from_cell(text: str) -> float | None:
    for line in split_cell_lines(text):
        if PRICE_ON_REQUEST_CHARS.search(line):
            continue
        if ALIAS_GROUP_PATTERN.search(line):
            return None
        if re.search(r"[A-Za-z]", line):
            continue
        parsed = parse_price(line)
        if parsed is not None and MIN_PURCHASE <= parsed <= MAX_PURCHASE:
            return round(parsed, 2)
    return None


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

    has_por_marker = any(PRICE_ON_REQUEST_CHARS.search(line) for line in lines)
    # POR markers inside descriptive text blocks typically mean MRP is
    # unavailable; numeric tokens in those blocks are often current/pack values.
    # Keep parsing only for numeric-only stacked cells (page-42 style).
    if has_por_marker and re.search(r"[A-Za-z]", " ".join(lines)):
        return []

    pairs: list[tuple[str, float]] = []
    pending_alias: str | None = None
    # Accumulate all valid prices after an alias; the LAST one is MRP
    # because stacked cells follow column order (alias, I_min, I_max, MRP).
    pending_prices: list[float] = []
    pending_price: float | None = None  # price-before-alias (reversed order)

    def _flush_pending() -> None:
        nonlocal pending_alias, pending_prices
        if pending_alias is not None and pending_prices:
            pairs.append((pending_alias, pending_prices[-1]))
        pending_alias = None
        pending_prices = []

    for line in lines:
        # Price-on-request markers indicate MRP missing for nearby variants.
        # Do not drop the whole cell; just break current alias->price chaining.
        if PRICE_ON_REQUEST_CHARS.search(line):
            _flush_pending()
            pending_price = None
            continue

        tokens = line.split()
        if len(tokens) >= 3:
            inline_pairs = _extract_inline_triplets(tokens)
            if inline_pairs:
                _flush_pending()
                pairs.extend(inline_pairs)
                pending_price = None
                continue

        if len(tokens) == 2 and _looks_like_alias_group(tokens[0], tokens[1]):
            alias = _normalize_alias(line)
            if looks_like_alias(alias, allow_numeric=True):
                _flush_pending()
                # If a price was seen on a preceding line, pair it now.
                if pending_price is not None:
                    pairs.append((alias, pending_price))
                    pending_price = None
                else:
                    pending_alias = alias
                    pending_prices = []
            continue

        if len(tokens) == 1 and _is_valid_price_token(tokens[0]):
            price_val = round(float(tokens[0]), 2)
            if pending_alias is not None:
                pending_prices.append(price_val)
            else:
                # Price before alias: remember for next alias line.
                pending_price = price_val
            continue

        # Numeric tokens below MIN_PURCHASE (e.g. current ratings 18A, 25A)
        # are common in stacked multi-column cells. Keep pending_alias alive
        # so the actual MRP further down the cell can still be captured.
        if len(tokens) == 1 and NUMERIC_TOKEN_PATTERN.fullmatch(tokens[0]):
            continue

        # Pack markers (e.g. 1/5/60) can appear between price and alias in
        # stacked cells. Keep pending chain so trailing alias still gets price.
        if re.fullmatch(r"\d+\s*/\s*\d+\s*/\s*\d+", line):
            continue

        _flush_pending()
        pending_price = None

    _flush_pending()
    return pairs


def _extract_adjacent_cell_pairs(row_cells: list[str]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    for idx in range(len(row_cells) - 1):
        alias_raw = row_cells[idx]
        price_raw = row_cells[idx + 1]
        alias = _extract_tail_alias_from_cell(alias_raw)
        if alias is None:
            continue

        self_pairs = _extract_row_pairs(alias_raw)
        self_pair_aliases = {pair_alias for pair_alias, _ in self_pairs}
        alias_lines = split_cell_lines(alias_raw)
        starts_with_price = False
        if alias_lines:
            first_line_price = parse_price(alias_lines[0])
            starts_with_price = first_line_price is not None and first_line_price >= MIN_PURCHASE
        if alias in self_pair_aliases and not starts_with_price:
            continue

        alias_is_simple_group = bool(re.fullmatch(r"\b\d{3,6}[ \t]\d{2,6}\b", alias_raw))
        alias_is_complex = (
            not alias_is_simple_group
            or "\n" in alias_raw
            or bool(PRICE_ON_REQUEST_CHARS.search(alias_raw))
            or starts_with_price
        )

        if alias_is_complex:
            purchase = _extract_leading_price_from_cell(price_raw)
            if purchase is None:
                continue
            pairs.append((alias, purchase))
            continue

        # Preserve strict behavior for simple alias-only cells so packed
        # price+pack stacks are handled by compact/mapped parsers.
        if re.search(r"[A-Za-z]", price_raw):
            continue
        purchase = parse_price(price_raw)
        if purchase is None or purchase < MIN_PURCHASE or purchase > MAX_PURCHASE:
            continue
        pairs.append((alias, round(purchase, 2)))
    return pairs


def _extract_spread_row_pairs(row_cells: list[str]) -> list[tuple[str, float]]:
    """Extract alias-price pairs when description cells sit between them.

    Example layout in one row: `Cat.No | Description ... 75 | MRP | Pack`.
    Adjacent alias->price parsing is intentionally strict to avoid grabbing
    description suffix numbers, so this helper recovers MRP from later numeric
    cells only when text cells appear between alias and the numeric price.
    """
    pairs: list[tuple[str, float]] = []
    for idx in range(len(row_cells) - 2):
        alias_raw = row_cells[idx]
        alias_tokens = alias_raw.split()
        if len(alias_tokens) != 2:
            continue
        first, second = alias_tokens
        if not _looks_like_alias_group(first, second):
            continue
        alias = _normalize_alias(alias_raw)
        if not looks_like_alias(alias, allow_numeric=True):
            continue

        seen_text_between = False
        chosen_price: float | None = None
        for cell in row_cells[idx + 1 :]:
            compact_cell = " ".join(cell.split()).strip()
            if not compact_cell:
                continue
            # If a no-price marker appears before a numeric token, treat the
            # item's MRP as unavailable and do not consume trailing pack values.
            if PRICE_ON_REQUEST_CHARS.search(compact_cell) or UNAVAILABLE_PRICE_MARKER_PATTERN.fullmatch(compact_cell):
                break
            if ALIAS_GROUP_PATTERN.search(cell):
                # A later alias cell starts a new item block on this row.
                # Do not let an earlier stale/gutter alias claim the new
                # item's price from further right columns.
                break
            if re.search(r"[A-Za-z]", cell):
                seen_text_between = True
                continue
            parsed = parse_price(cell)
            if parsed is None or parsed < MIN_PURCHASE or parsed > MAX_PURCHASE:
                continue
            if seen_text_between:
                chosen_price = round(parsed, 2)
                break

        if chosen_price is not None:
            pairs.append((alias, chosen_price))
    return pairs


def extract_alias_price_stream_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
    min_rows: int = 4,
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
        for target in row_cells:
            row_pairs.extend(_extract_row_pairs(target))
        row_pairs.extend(_extract_adjacent_cell_pairs(row_cells))
        row_pairs.extend(_extract_spread_row_pairs(row_cells))

        if not row_pairs:
            continue

        best_row_purchase_by_alias: dict[str, float] = {}
        for alias, purchase in row_pairs:
            best_row_purchase_by_alias[alias] = purchase
        row_pairs = list(best_row_purchase_by_alias.items())

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
    return rows if len(rows) >= min_rows else []
