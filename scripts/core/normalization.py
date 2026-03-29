"""Row normalization with multi-layout support (packed, sparse, vertical)."""

from __future__ import annotations

import re

from core.header import (
    build_column_mappings,
    detect_header_row_index,
    enrich_header_row,
    has_alias_header_evidence,
    infer_sparse_row_mappings,
)
from core.dense_column import extract_dense_column_rows
from core.compact_vertical import extract_compact_vertical_rows
from core.alias_price_stream import extract_alias_price_stream_rows
from core.models import NormalizedRow
from core.parsing import clean_pack, extract_alias, looks_like_alias, parse_price
from core.quality_scoring import (
    line_alias_count,
    line_pack_count,
    line_price_count,
    normalized_row_quality,
    select_pack_column,
)
from core.table_analysis import extract_horizontal_table_rows
from core.text_utils import extract_alias_entries, fallback_particulars, split_cell_lines


def collapse_matrix_to_single_row(matrix: list[list[str]]) -> list[str] | None:
    """Join non-empty column fragments across rows into a synthetic row.

    Some tables are split into multiple horizontal fragments where related
    columns are distributed over different matrix rows.
    """
    if not matrix:
        return None

    max_cols = max((len(row) for row in matrix), default=0)
    if max_cols == 0:
        return None

    non_empty_counts = [sum(1 for cell in row if cell and cell.strip()) for row in matrix]
    sparse_rows = sum(1 for c in non_empty_counts if c <= 1)
    # Collapse only when matrix looks vertically fragmented
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
    """Fallback parser for tables extracted without a header row.

    Some Camelot outputs merge many logical rows into multiline cells. This parser
    infers alias/price columns per row and expands line-wise values into rows.
    """
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

            # Split non-alias/price/pack text columns into lines
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
                    # For each text column pick the line at position i if available
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

    # Keep best candidate per alias+purchase from this fallback pass
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

    # Merge continuation rows into the preceding data row's particulars column
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
            and particulars_col is not None
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


def normalize_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
) -> list[NormalizedRow]:
    """Normalize table matrix into validated rows using best-fit layout detection.
    
    Tries in order:
    1. Horizontal layout (configs in columns, roles in rows)
    2. Standard vertical with header mapping
    3. Sparse row-wise (inferred layout)
    4. Packed multiline (fallback for complex cells)
    """
    if len(matrix) < 2:
        return []

    def _merge_best(rows: list[NormalizedRow], extra_rows: list[NormalizedRow]) -> list[NormalizedRow]:
        best_by_key: dict[tuple[str, float], NormalizedRow] = {}
        for row in rows + extra_rows:
            key = (row.alias, row.purchase)
            existing = best_by_key.get(key)
            if existing is None or normalized_row_quality(row) > normalized_row_quality(existing):
                best_by_key[key] = row
        return list(best_by_key.values())

    # Check for horizontal table layout FIRST (before header mapping)
    horizontal_rows = extract_horizontal_table_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
    )
    if len(horizontal_rows) >= 3:
        return horizontal_rows

    header_row_idx = detect_header_row_index(matrix)
    headers = matrix[header_row_idx] if 0 <= header_row_idx < len(matrix) else []
    scored_headers = enrich_header_row(matrix, base_row_idx=header_row_idx, base_headers=headers)
    mappings = build_column_mappings(scored_headers)
    if not mappings:
        # Fall back to vertical table layouts
        compact_vertical_rows = extract_compact_vertical_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        if len(compact_vertical_rows) >= 4:
            stream_rows = extract_alias_price_stream_rows(
                matrix,
                page_number=page_number,
                include_particulars=include_particulars,
                include_pack=include_pack,
            )
            return _merge_best(compact_vertical_rows, stream_rows)

        dense_rows = extract_dense_column_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        if len(dense_rows) >= 3:
            stream_rows = extract_alias_price_stream_rows(
                matrix,
                page_number=page_number,
                include_particulars=include_particulars,
                include_pack=include_pack,
            )
            return _merge_best(dense_rows, stream_rows)

        sparse_rows = extract_sparse_rowwise_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        # Only run packed fallback when sparse finds nothing — packed parser can
        # misread image-label rows and emit spurious entries
        packed_rows = [] if len(sparse_rows) >= 3 else extract_packed_multiline_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )

        best_by_key: dict[tuple[str, float], NormalizedRow] = {}
        for row in sparse_rows + packed_rows:
            key = (row.alias, row.purchase)
            current = best_by_key.get(key)
            if current is None or normalized_row_quality(row) > normalized_row_quality(current):
                best_by_key[key] = row
        return list(best_by_key.values())

    data_rows = matrix[header_row_idx + 1 :]

    # Some mixed blocks place small unit/rating values under the mapped
    # purchase column while real prices appear in a neighboring mapped pack
    # column. Swap only when value distributions strongly support it.
    for mapping in mappings:
        if "pack" not in mapping:
            continue
        purchase_idx = mapping["purchase"]
        pack_idx = mapping["pack"]
        purchase_vals = [
            parsed
            for row in data_rows
            for parsed in [parse_price(row[purchase_idx].strip() if purchase_idx < len(row) else "")]
            if parsed is not None
        ]
        pack_vals = [
            parsed
            for row in data_rows
            for parsed in [parse_price(row[pack_idx].strip() if pack_idx < len(row) else "")]
            if parsed is not None
        ]
        if len(purchase_vals) < 5 or len(pack_vals) < 5:
            continue
        purchase_small_ratio = sum(1 for v in purchase_vals if v <= 200) / len(purchase_vals)
        pack_high_ratio = sum(1 for v in pack_vals if v >= 500) / len(pack_vals)
        if purchase_small_ratio >= 0.7 and pack_high_ratio >= 0.5 and max(pack_vals) > max(purchase_vals) * 2:
            mapping["purchase"], mapping["pack"] = mapping["pack"], mapping["purchase"]

    normalized: list[NormalizedRow] = []

    for row_idx, row in enumerate(data_rows):
        if not any(cell.strip() for cell in row):
            continue
        for mapping in mappings:
            alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
            price_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""
            allow_numeric_alias = (
                mapping["alias"] < len(scored_headers)
                and has_alias_header_evidence(scored_headers[mapping["alias"]])
            )

            alias = extract_alias(alias_raw, allow_numeric=allow_numeric_alias)
            purchase = parse_price(price_raw)

            if (
                purchase is None
                and alias_raw
                and row_idx + 1 < len(data_rows)
            ):
                next_row = data_rows[row_idx + 1]
                next_alias_raw = next_row[mapping["alias"]].strip() if mapping["alias"] < len(next_row) else ""
                next_price_raw = next_row[mapping["purchase"]].strip() if mapping["purchase"] < len(next_row) else ""
                next_purchase = parse_price(next_price_raw)
                next_pack_raw = ""
                if "pack" in mapping and mapping["pack"] < len(next_row):
                    next_pack_raw = next_row[mapping["pack"]].strip()
                if next_purchase is not None and not next_alias_raw and not next_pack_raw:
                    purchase = next_purchase

            if not looks_like_alias(alias, allow_numeric=allow_numeric_alias) or purchase is None:
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

            normalized.append(
                NormalizedRow(
                    particulars=particulars,
                    alias=alias,
                    purchase=round(purchase, 2),
                    pack=pack,
                    source_page=page_number,
                )
            )

    if normalized:
        stream_rows = extract_alias_price_stream_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        if not stream_rows:
            return normalized
        return _merge_best(normalized, stream_rows)

    compact_vertical_rows = extract_compact_vertical_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
    )
    if len(compact_vertical_rows) >= 4:
        stream_rows = extract_alias_price_stream_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        return _merge_best(compact_vertical_rows, stream_rows)

    # Some layouts collapse alias and purchase values into one dense text
    # column while keeping pack in a neighboring column.
    dense_rows = extract_dense_column_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
    )
    if len(dense_rows) >= 3:
        stream_rows = extract_alias_price_stream_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        return _merge_best(dense_rows, stream_rows)

    stream_rows = extract_alias_price_stream_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
    )
    if len(stream_rows) >= 4:
        return stream_rows

    sparse_rows = extract_sparse_rowwise_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
    )
    # When deterministic header mapping exists, do not fall back to packed
    # multiline parsing. Packed fallback is intended for headerless/collapsed
    # tables and can manufacture false positives when a mapped purchase column
    # exists but contains no parseable prices.
    packed_rows: list[NormalizedRow] = []

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in sparse_rows + packed_rows:
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row
    return list(best_by_key.values())
