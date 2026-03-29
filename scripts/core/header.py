"""Header detection and column mapping."""

from __future__ import annotations

import re
from typing import Iterable

from rapidfuzz import fuzz

from core.models import ACTIVE_ROLE_SYNONYMS
from core.parsing import clean_pack, looks_like_alias_line, parse_price
from core.text_utils import normalize_header


def header_role_score(header: str, role: str) -> int:
    """Score how well a header matches a given role using fuzzy matching.
    
    Returns 0-100 where 100 is perfect match against role synonyms from profile.
    """
    normalized = normalize_header(header)
    if not normalized:
        return 0

    best = 0
    for synonym in ACTIVE_ROLE_SYNONYMS[role]:
        synonym_n = normalize_header(synonym)
        if synonym_n in normalized:
            best = max(best, 100)
        best = max(best, int(fuzz.partial_ratio(normalized, synonym_n)))
    return best


def first_non_empty_row(matrix: list[list[str]]) -> list[str]:
    """Find first row with at least one non-empty cell."""
    for row in matrix:
        if any(cell.strip() for cell in row):
            return row
    return []


def enrich_header_row(
    matrix: list[list[str]],
    base_row_idx: int,
    base_headers: list[str],
    lookahead_rows: int = 2,
) -> list[str]:
    """Combine neighboring header rows into a richer per-column header string.

    This helps with multi-line/multi-row headers where role text is split across
    stacked rows (e.g. "Reference No." appears in the second header row).
    """
    if not matrix or not base_headers:
        return base_headers

    max_cols = max((len(row) for row in matrix), default=0)
    if max_cols == 0:
        return base_headers

    enriched: list[str] = []
    end_row = min(len(matrix), base_row_idx + lookahead_rows + 1)

    for col_idx in range(max_cols):
        parts: list[str] = []
        for row_idx in range(base_row_idx, end_row):
            row = matrix[row_idx]
            if col_idx >= len(row):
                continue
            cell = " ".join(row[col_idx].split()).strip()
            if cell and cell not in parts:
                parts.append(cell)
        enriched.append(" ".join(parts).strip())

    # Keep original width when matrix has fewer detected columns.
    if len(enriched) < len(base_headers):
        return base_headers
    return enriched[: len(base_headers)]


def nearest_index(target: int, choices: Iterable[int]) -> int | None:
    """Find nearest column index to target from candidates."""
    choices_list = list(choices)
    if not choices_list:
        return None
    return min(choices_list, key=lambda x: abs(x - target))


def build_column_mappings(headers: list[str]) -> list[dict[str, int]]:
    """Build column role mappings from headers using fuzzy matching.
    
    Handles multiple alias/price blocks on same row (e.g., repeated headers).
    Returns list of {role: column_index} dicts, one per data block.
    """
    if not headers:
        return []

    scores_by_role: dict[str, list[int]] = {
        role: [header_role_score(h, role) for h in headers] for role in ACTIVE_ROLE_SYNONYMS
    }

    alias_cols = [i for i, s in enumerate(scores_by_role["alias"]) if s >= 70]
    purchase_cols = [i for i, s in enumerate(scores_by_role["purchase"]) if s >= 70]
    particulars_cols = [i for i, s in enumerate(scores_by_role["particulars"]) if s >= 70]
    pack_cols = [
        i
        for i, s in enumerate(scores_by_role["pack"])
        if s >= 70 and any(token in normalize_header(headers[i]) for token in ("pack", "pkg", "nos", "uom"))
    ]

    mappings: list[dict[str, int]] = []

    # Handle repeated header blocks. Some catalogs have more alias columns than
    # purchase columns (e.g. white+grey share one MRP column, black has another).
    if len(alias_cols) >= 2 and len(purchase_cols) >= 1:
        used_purchase: set[int] = set()
        for alias_idx in sorted(alias_cols):
            # If aliases outnumber purchase columns, allow reuse of nearest
            # purchase column so multiple color variants can share the same MRP.
            if len(alias_cols) > len(purchase_cols):
                candidate_purchase = list(purchase_cols)
                # In shared-price mode, when two purchase columns are equally
                # close, prefer the purchase column to the right. This avoids
                # incorrectly collapsing right-side variants into left prices.
                p_idx = min(
                    candidate_purchase,
                    key=lambda p: (abs(p - alias_idx), 0 if p >= alias_idx else 1, p),
                )
            else:
                candidate_purchase = [p for p in purchase_cols if p not in used_purchase]
                p_idx = nearest_index(alias_idx, candidate_purchase)
            if p_idx is None:
                continue
            if len(alias_cols) <= len(purchase_cols):
                used_purchase.add(p_idx)

            mapping = {"alias": alias_idx, "purchase": p_idx}
            part_idx = nearest_index(alias_idx, particulars_cols)
            pack_idx = nearest_index(alias_idx, pack_cols)
            if part_idx is not None and abs(part_idx - alias_idx) <= 4:
                mapping["particulars"] = part_idx
            if pack_idx is not None and (
                abs(pack_idx - alias_idx) <= 4 or (len(pack_cols) == 1 and pack_idx > alias_idx)
            ):
                mapping["pack"] = pack_idx
            mappings.append(mapping)

    if mappings:
        return mappings

    # Single mapping case
    alias_best = max(range(len(headers)), key=lambda i: scores_by_role["alias"][i])
    purchase_best = max(range(len(headers)), key=lambda i: scores_by_role["purchase"][i])
    if scores_by_role["alias"][alias_best] < 60 or scores_by_role["purchase"][purchase_best] < 60:
        return []

    mapping = {"alias": alias_best, "purchase": purchase_best}
    if particulars_cols:
        mapping["particulars"] = max(particulars_cols, key=lambda i: scores_by_role["particulars"][i])
    if pack_cols:
        preferred_pack = max(pack_cols, key=lambda i: scores_by_role["pack"][i])
        if abs(preferred_pack - alias_best) <= 4 or (len(pack_cols) == 1 and preferred_pack > alias_best):
            mapping["pack"] = preferred_pack
    return [mapping]


def infer_sparse_row_mappings(matrix: list[list[str]]) -> list[dict[str, int]]:
    """Infer column roles from cell distributions when header mapping fails.
    
    Analyzes which columns contain aliases, prices, and pack values.
    """
    if not matrix:
        return []

    max_cols = max((len(row) for row in matrix), default=0)
    if max_cols == 0:
        return []

    alias_scores = [0] * max_cols
    purchase_scores = [0] * max_cols
    pack_scores = [0] * max_cols
    particulars_scores = [0] * max_cols
    numeric_values_by_col: list[list[float]] = [[] for _ in range(max_cols)]
    text_richness_scores = [0] * max_cols
    pack_strength_scores = [0] * max_cols

    for row in matrix:
        for idx in range(max_cols):
            cell = row[idx] if idx < len(row) else ""
            value = " ".join(cell.split()).strip()
            if not value:
                continue

            if looks_like_alias_line(value):
                alias_scores[idx] += 1
                continue

            parsed_numeric = parse_price(value)
            if parsed_numeric is not None:
                purchase_scores[idx] += 1
                numeric_values_by_col[idx].append(parsed_numeric)

            if clean_pack(value):
                pack_scores[idx] += 1

                if re.fullmatch(r"\d+\s*/\s*\d+", value):
                    pack_strength_scores[idx] += 4
                elif re.fullmatch(r"\d+", value):
                    pack_strength_scores[idx] += 3
                elif re.search(r"[A-Za-z]", value):
                    pack_strength_scores[idx] -= 2

            if parsed_numeric is None and re.search(r"[A-Za-z]", value):
                particulars_scores[idx] += 1
                text_richness_scores[idx] += len(re.findall(r"[A-Za-z]", value))

    alias_cols = [i for i, s in enumerate(alias_scores) if s >= 3]
    purchase_cols: list[int] = []
    for i, s in enumerate(purchase_scores):
        if s < 3:
            continue
        nums = numeric_values_by_col[i]
        if nums:
            small_ratio = sum(1 for n in nums if n.is_integer() and 0 < n <= 100) / len(nums)
            if small_ratio >= 0.7:
                continue
        purchase_cols.append(i)
    pack_cols = [i for i, s in enumerate(pack_scores) if s >= 3]
    particulars_cols = [i for i, s in enumerate(particulars_scores) if s >= 3]

    mappings: list[dict[str, int]] = []
    used_purchase: set[int] = set()

    def alias_price_cooccurrence(alias_idx: int, purchase_idx: int) -> int:
        count = 0
        for row in matrix:
            alias_cell = row[alias_idx] if alias_idx < len(row) else ""
            purchase_cell = row[purchase_idx] if purchase_idx < len(row) else ""
            if looks_like_alias_line(alias_cell.strip()) and parse_price(purchase_cell.strip()) is not None:
                count += 1
        return count

    def pack_cooccurrence(alias_idx: int, purchase_idx: int, pack_idx: int) -> int:
        count = 0
        for row in matrix:
            alias_cell = row[alias_idx] if alias_idx < len(row) else ""
            purchase_cell = row[purchase_idx] if purchase_idx < len(row) else ""
            pack_cell = row[pack_idx] if pack_idx < len(row) else ""
            if (
                looks_like_alias_line(alias_cell.strip())
                and parse_price(purchase_cell.strip()) is not None
                and clean_pack(pack_cell.strip())
            ):
                count += 1
        return count

    for alias_idx in alias_cols:
        if len(alias_cols) > len(purchase_cols):
            # Shared-price layout: multiple alias columns can map to the same
            # purchase column (e.g. white+grey share one MRP, black has another).
            available_purchase = list(purchase_cols)
        else:
            available_purchase = [p for p in purchase_cols if p not in used_purchase]
        if not available_purchase:
            continue

        scored_purchase = sorted(
            available_purchase,
            key=lambda p: (alias_price_cooccurrence(alias_idx, p), -abs(p - alias_idx), 1 if p > alias_idx else 0, p),
            reverse=True,
        )
        p_idx = scored_purchase[0] if scored_purchase else None
        if p_idx is None:
            continue
        purchase_idx = p_idx
        if alias_price_cooccurrence(alias_idx, purchase_idx) < 3:
            continue
        if len(alias_cols) <= len(purchase_cols):
            used_purchase.add(purchase_idx)

        mapping = {"alias": alias_idx, "purchase": purchase_idx}

        candidate_pack_cols = [c for c in pack_cols if c != alias_idx and c != purchase_idx]
        if candidate_pack_cols:
            scored_pack = sorted(
                candidate_pack_cols,
                key=lambda c: (
                    pack_strength_scores[c],
                    pack_cooccurrence(alias_idx, purchase_idx, c),
                    1 if c > purchase_idx else 0,
                    -abs(c - purchase_idx),
                    c,
                ),
                reverse=True,
            )
            pack_idx = scored_pack[0] if scored_pack else None
            if pack_idx is not None:
                mapping["pack"] = pack_idx

        candidate_part_cols = [c for c in particulars_cols if c != alias_idx and c != purchase_idx]
        if candidate_part_cols:
            scored_parts = sorted(
                candidate_part_cols,
                key=lambda c: (
                    text_richness_scores[c],
                    particulars_scores[c],
                    -abs(c - alias_idx),
                    c,
                ),
                reverse=True,
            )
            part_idx = scored_parts[0] if scored_parts else None
            if part_idx is not None and abs(part_idx - alias_idx) <= 4:
                mapping["particulars"] = part_idx

        mappings.append(mapping)

    return mappings
