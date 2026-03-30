"""Row normalization with multi-layout support (packed, sparse, vertical)."""

from __future__ import annotations

from core.alias_price_stream import extract_alias_price_stream_rows
from core.compact_vertical import extract_compact_vertical_rows
from core.dense_column import extract_dense_column_rows
from core.header import build_column_mappings, detect_header_row_index, enrich_header_row
from core.models import NormalizedRow
from core.normalization_fallbacks import extract_packed_multiline_rows, extract_sparse_rowwise_rows
from core.normalization_helpers import is_current_like_purchase
from core.normalization_mapped import extract_with_mappings
from core.parsing import parse_price
from core.quality_scoring import normalized_row_quality
from core.table_analysis import extract_horizontal_table_rows


def _merge_best(rows: list[NormalizedRow], extra_rows: list[NormalizedRow]) -> list[NormalizedRow]:
    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in rows + extra_rows:
        key = (row.alias, row.purchase)
        existing = best_by_key.get(key)
        if existing is None or normalized_row_quality(row) > normalized_row_quality(existing):
            best_by_key[key] = row
    return list(best_by_key.values())


def normalize_rows(
    matrix: list[list[str]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
    inherited_mappings: list[dict[str, int]] | None = None,
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
        if inherited_mappings:
            inherited_rows = extract_with_mappings(
                matrix,
                inherited_mappings,
                page_number=page_number,
                include_particulars=include_particulars,
                include_pack=include_pack,
                scored_headers=None,
                force_numeric_alias=True,
            )
            if inherited_rows:
                stream_rows = extract_alias_price_stream_rows(
                    matrix,
                    page_number=page_number,
                    include_particulars=include_particulars,
                    include_pack=include_pack,
                )
                return _merge_best(inherited_rows, stream_rows)

        stream_rows = extract_alias_price_stream_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )

        compact_vertical_rows = extract_compact_vertical_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        if len(compact_vertical_rows) >= 4:
            return _merge_best(compact_vertical_rows, stream_rows)

        dense_rows = extract_dense_column_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        if len(dense_rows) >= 3:
            return _merge_best(dense_rows, stream_rows)

        if len(stream_rows) >= 4:
            return stream_rows

        sparse_rows = extract_sparse_rowwise_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )

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
        if best_by_key:
            return list(best_by_key.values())

        if not stream_rows:
            stream_rows = extract_alias_price_stream_rows(
                matrix,
                page_number=page_number,
                include_particulars=include_particulars,
                include_pack=include_pack,
                min_rows=2,
            )
        return stream_rows

    data_rows = matrix[header_row_idx + 1 :]

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

    normalized = extract_with_mappings(
        data_rows,
        mappings,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
        scored_headers=scored_headers,
    )

    compact_vertical_rows = extract_compact_vertical_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
    )

    if normalized:
        stream_rows = extract_alias_price_stream_rows(
            matrix,
            page_number=page_number,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        stream_rows = [r for r in stream_rows if not is_current_like_purchase(r.purchase)]
        mapped_max_purchase: dict[str, float] = {}
        mapped_alias_prices: dict[str, set[float]] = {}
        for row in normalized:
            mapped_max_purchase[row.alias] = max(mapped_max_purchase.get(row.alias, row.purchase), row.purchase)
            mapped_alias_prices.setdefault(row.alias, set()).add(row.purchase)

        filtered_stream_rows: list[NormalizedRow] = []
        for row in stream_rows:
            mapped_purchase = mapped_max_purchase.get(row.alias)
            if mapped_purchase is not None and row.purchase < mapped_purchase:
                continue
            # Suppress flattened footnote aliases (e.g. 4122761) when the base
            # alias (412276) already exists with the same purchase.
            if row.alias.isdigit() and len(row.alias) >= 7:
                base_alias = row.alias[:-1]
                if base_alias in mapped_alias_prices and row.purchase in mapped_alias_prices[base_alias]:
                    continue
            filtered_stream_rows.append(row)
        stream_rows = filtered_stream_rows
        extras = stream_rows + compact_vertical_rows
        if not extras:
            return normalized
        return _merge_best(normalized, extras)

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

    packed_rows: list[NormalizedRow] = []

    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in sparse_rows + packed_rows:
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row
    if best_by_key:
        return list(best_by_key.values())

    relaxed_stream = extract_alias_price_stream_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
        min_rows=2,
    )
    return relaxed_stream
