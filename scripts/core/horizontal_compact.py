"""Parser for compact horizontal tables collapsed into one dense text column."""

from __future__ import annotations

import re

from core.models import NormalizedRow
from core.parsing import clean_alias, clean_pack, looks_like_alias, parse_price
from core.role_markers import has_role_marker
from core.text_utils import split_cell_lines


def _dominant_column(matrix: list[list[str]]) -> int | None:
    max_cols = max((len(row) for row in matrix), default=0)
    if max_cols == 0:
        return None

    counts = [0] * max_cols
    for row in matrix:
        for idx in range(max_cols):
            if idx < len(row) and row[idx].strip():
                counts[idx] += 1

    best_idx = max(range(max_cols), key=lambda i: counts[i])
    if counts[best_idx] < 2:
        return None
    return best_idx


def _extract_aliases(lines: list[str]) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()

    for line in lines:
        if has_role_marker(line, "alias", include_role_name=True):
            continue

        # Handle both one-token and space-separated alias groups.
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}", line.upper())
        for token in tokens:
            alias = clean_alias(token)
            if looks_like_alias(alias) and alias not in seen:
                aliases.append(alias)
                seen.add(alias)

    return aliases


def _extract_ref_labels(lines: list[str]) -> list[str]:
    labels: list[str] = []
    for line in lines:
        if has_role_marker(line, "alias", include_role_name=True):
            continue
        if parse_price(line) is not None:
            continue
        if looks_like_alias(clean_alias(line)):
            continue
        if line.strip():
            labels.append(line.strip())
    return labels


def _extract_purchase_pack_labels(
    lines: list[str],
    include_pack: bool,
    include_particulars: bool,
) -> tuple[list[float], list[str], list[str]]:
    prices: list[float] = []
    packs: list[str] = []
    labels: list[str] = []

    pack_section = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped:
            continue

        if has_role_marker(lower, "purchase", include_role_name=True):
            continue

        if include_pack and has_role_marker(lower, "pack", include_role_name=True):
            pack_section = True
            continue

        parsed = parse_price(stripped)
        if parsed is not None:
            if include_pack and pack_section and parsed.is_integer() and 0 < parsed <= 100:
                packs.append(clean_pack(str(int(parsed))))
            else:
                prices.append(parsed)
            continue

        if include_particulars:
            labels.append(stripped)

    return prices, packs, labels


def extract_compact_horizontal_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Extract rows from compact horizontal layout collapsed into one text column.

    Camelot can collapse some horizontal tables into a single dense text column,
    where each logical block appears as:
    - reference row (aliases + "Reference No.")
    - purchase row (particulars + prices + "Unit MRP")
    - optional pack values (often appended to final purchase row)
    """
    dominant_col = _dominant_column(matrix)
    if dominant_col is None:
        return []

    role_rows: list[tuple[int, str]] = []
    for row_idx, row in enumerate(matrix):
        value = row[dominant_col].strip() if dominant_col < len(row) else ""
        if not value:
            continue
        role_rows.append((row_idx, value))

    if not role_rows:
        return []

    out: list[NormalizedRow] = []

    for idx, (_, text) in enumerate(role_rows):
        if not has_role_marker(text, "alias", include_role_name=True):
            continue

        if idx + 1 >= len(role_rows):
            continue

        _, next_text = role_rows[idx + 1]
        if not has_role_marker(next_text, "purchase", include_role_name=True):
            continue

        ref_lines = split_cell_lines(text)
        purchase_lines = split_cell_lines(next_text)

        aliases = _extract_aliases(ref_lines)
        prices, packs, purchase_labels = _extract_purchase_pack_labels(
            purchase_lines,
            include_pack=include_pack,
            include_particulars=include_particulars,
        )
        ref_labels = _extract_ref_labels(ref_lines) if include_particulars else []

        if not aliases or not prices:
            continue

        if len(prices) == 1 and len(aliases) > 1:
            prices = prices * len(aliases)

        row_count = min(len(aliases), len(prices))
        if row_count == 0:
            continue

        particulars = ""
        if include_particulars:
            particulars = " ".join(part for part in (ref_labels + purchase_labels) if part).strip()

        for item_idx in range(row_count):
            pack = ""
            if include_pack:
                if len(packs) == row_count:
                    pack = packs[item_idx]
                elif len(packs) == 1:
                    pack = packs[0]

            out.append(
                NormalizedRow(
                    particulars=particulars,
                    alias=aliases[item_idx],
                    purchase=round(prices[item_idx], 2),
                    pack=pack,
                    source_page=page_number,
                )
            )

    return out
