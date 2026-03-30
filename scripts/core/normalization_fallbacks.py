"""Fallback normalizers for sparse and packed table layouts."""

from __future__ import annotations

import re

from core.header import infer_sparse_row_mappings
from core.models import NormalizedRow
from core.parsing import clean_pack, extract_alias, looks_like_alias, parse_price
from core.quality_scoring import (
    line_alias_count,
    line_pack_count,
    line_price_count,
    normalized_row_quality,
    select_pack_column,
)
from core.text_utils import extract_alias_entries, fallback_particulars, split_cell_lines


def collapse_matrix_to_single_row(matrix: list[list[str]]) -> list[str] | None:
    """Join non-empty column fragments across rows into a synthetic row."""
    if not matrix:
        return None

    max_cols = max((len(row) for row in matrix), default=0)
    if max_cols == 0:
        return None

    non_empty_counts = [sum(1 for cell in row if cell and cell.strip()) for row in matrix]
    sparse_rows = sum(1 for c in non_empty_counts if c <= 1)
    if len(matrix) < 4 or sparse_rows < 2:
        return None

    chunks: list[list[str]] = [[] for _ in range(max_cols)]
    for row in matrix:
        for idx in range(max_cols):
            cell = row[idx] if idx < len(row) else ""
            if cell and cell.strip():
                chunks[idx].append(cell.strip())

    non_empty_cols = sum(1 for col in chunks if col)
    if non_empty_cols < 2:
        return None

    collapsed = ["\n".join(col) if col else "" for col in chunks]
    if collapsed in matrix:
        return None
    return collapsed


def extract_packed_multiline_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Fallback parser for tables extracted without a reliable header row."""
    normalized: list[NormalizedRow] = []

    rows_to_process = list(matrix)
    collapsed_row = collapse_matrix_to_single_row(matrix)
    if collapsed_row is not None:
        rows_to_process = [collapsed_row] + rows_to_process

    for row in rows_to_process:
        if not any(cell.strip() for cell in row):
            continue

        alias_cols = [idx for idx, cell in enumerate(row) if line_alias_count(cell) >= 2]
        purchase_cols = [
            idx for idx, cell in enumerate(row) if line_price_count(cell) >= 2 and line_pack_count(cell) < 2
        ]
        pack_cols = [idx for idx, cell in enumerate(row) if line_pack_count(cell) >= 2] if include_pack else []

        if not alias_cols or not purchase_cols:
            continue

        pairs: list[tuple[int, int]] = []
        used_purchase: set[int] = set()
        for alias_idx in sorted(alias_cols):
            def nearest_index(target: int, choices):
                choices_list = list(choices)
                if not choices_list:
                    return None
                return min(choices_list, key=lambda x: abs(x - target))

            p_idx = nearest_index(alias_idx, [p for p in purchase_cols if p not in used_purchase])
            if p_idx is None:
                continue
            used_purchase.add(p_idx)
            pairs.append((alias_idx, p_idx))

        if not pairs:
            continue

        for alias_idx, purchase_idx in pairs:
            alias_entries = extract_alias_entries(row[alias_idx])
            alias_lines = [entry[0] for entry in alias_entries]
            purchase_lines = split_cell_lines(row[purchase_idx])

            pack_lines: list[str] = []
            pack_idx = select_pack_column(alias_idx, pack_cols, row) if include_pack else None
            if include_pack and pack_idx is not None:
                from core.text_utils import split_pack_tokens

                pack_lines = split_pack_tokens(row[pack_idx])

            used_cols = {alias_idx, purchase_idx}
            if pack_idx is not None:
                used_cols.add(pack_idx)
            text_col_lines: list[list[str]] = []
            if include_particulars:
                text_col_lines = [
                    split_cell_lines(cell)
                    for idx, cell in enumerate(row)
                    if idx not in used_cols
                    and cell.strip()
                    and line_alias_count(cell) == 0
                    and line_price_count(cell) == 0
                ]

            max_len = max(len(alias_lines), len(purchase_lines))
            for i in range(max_len):
                alias = alias_lines[i] if i < len(alias_lines) else ""
                alias_particulars = alias_entries[i][1] if include_particulars and i < len(alias_entries) else ""
                purchase = parse_price(purchase_lines[i]) if i < len(purchase_lines) else None
                if not looks_like_alias(alias) or purchase is None:
                    continue

                pack = ""
                if i < len(pack_lines):
                    pack = clean_pack(pack_lines[i])

                particulars = ""
                if include_particulars:
                    parts: list[str] = []
                    for col_lines in text_col_lines:
                        if not col_lines:
                            continue
                        if i < len(col_lines):
                            val = col_lines[i].strip()
                        elif len(col_lines) == 1:
                            val = col_lines[0].strip()
                        else:
                            continue
                        if val and not re.fullmatch(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$", val.replace(",", "")):
                            parts.append(val)
                    particulars = " / ".join(parts)
                    if not particulars:
                        particulars = alias_particulars

                normalized.append(
                    NormalizedRow(
                        particulars=particulars,
                        alias=alias,
                        purchase=round(purchase, 2),
                        pack=pack,
                        source_page=page_number,
                    )
                )

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in normalized:
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row

    return list(best_by_key.values())


def extract_sparse_rowwise_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Parse row-wise sparse tables where cell backgrounds separate columns."""
    mappings = infer_sparse_row_mappings(matrix)
    if not mappings:
        return []

    alias_cols_set = {m["alias"] for m in mappings}
    purchase_cols_set = {m["purchase"] for m in mappings}
    particulars_col: int | None = (
        next((m["particulars"] for m in mappings if "particulars" in m), None)
        if include_particulars
        else None
    )

    working_matrix: list[list[str]] = []
    for row in matrix:
        if not any(cell.strip() for cell in row):
            continue
        is_continuation = (
            particulars_col is not None
            and particulars_col < len(row)
            and row[particulars_col].strip()
            and all((row[c] if c < len(row) else "").strip() == "" for c in alias_cols_set | purchase_cols_set)
        )
        if is_continuation and working_matrix:
            prev = list(working_matrix[-1])
            part_idx = particulars_col
            if part_idx is not None and part_idx < len(prev):
                prev[part_idx] = prev[part_idx] + " " + row[part_idx].strip()
                working_matrix[-1] = prev
        else:
            working_matrix.append(list(row))

    out: list[NormalizedRow] = []
    for row in working_matrix:
        for mapping in mappings:
            alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
            purchase_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""

            alias = extract_alias(alias_raw)
            purchase = parse_price(purchase_raw)
            if not looks_like_alias(alias) or purchase is None:
                continue

            pack = ""
            if include_pack and "pack" in mapping and mapping["pack"] < len(row):
                pack = clean_pack(row[mapping["pack"]])

            particulars = ""
            if include_particulars:
                if "particulars" in mapping and mapping["particulars"] < len(row):
                    particulars = " ".join(row[mapping["particulars"]].split()).strip()
                if not particulars:
                    used = {mapping["alias"], mapping["purchase"]}
                    if "pack" in mapping:
                        used.add(mapping["pack"])
                    particulars = fallback_particulars(row, used)

            out.append(
                NormalizedRow(
                    particulars=particulars,
                    alias=alias,
                    purchase=round(purchase, 2),
                    pack=pack,
                    source_page=page_number,
                )
            )

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in out:
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row
    return list(best_by_key.values())
