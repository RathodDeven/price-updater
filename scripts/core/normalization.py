"""Row normalization with multi-layout support (packed, sparse, vertical)."""

from __future__ import annotations

import re

from core.header import (
    build_column_mappings,
    detect_header_row_index,
    enrich_header_row,
    has_alias_header_evidence,
    has_purchase_header_evidence,
    infer_sparse_row_mappings,
)
from core.dense_column import extract_dense_column_rows
from core.compact_vertical import extract_compact_vertical_rows
from core.alias_price_stream import extract_alias_price_stream_rows, _extract_row_pairs
from core.models import NormalizedRow
from core.parsing import (
    PRICE_ON_REQUEST_PATTERN,
    clean_pack,
    extract_alias,
    looks_like_alias,
    looks_like_alias_line,
    parse_price,
)
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

    def _merge_best(rows: list[NormalizedRow], extra_rows: list[NormalizedRow]) -> list[NormalizedRow]:
        best_by_key: dict[tuple[str, float], NormalizedRow] = {}
        for row in rows + extra_rows:
            key = (row.alias, row.purchase)
            existing = best_by_key.get(key)
            if existing is None or normalized_row_quality(row) > normalized_row_quality(existing):
                best_by_key[key] = row
        return list(best_by_key.values())

    def _inline_alias_price_pair(text: str) -> tuple[str, float] | None:
        """Extract an inline alias+price pair from one mixed cell when present.

        Handles rows where alias and purchase are embedded together in the alias
        cell (for example split/right-side blocks with no standalone purchase
        column for that sub-table).
        """
        best_pair: tuple[str, float] | None = None
        for line in split_cell_lines(text):
            tokens = line.replace(",", " ").split()
            if len(tokens) < 3:
                continue
            for idx in range(len(tokens) - 2):
                first, second, price_token = tokens[idx], tokens[idx + 1], tokens[idx + 2]
                if not (first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6):
                    continue
                parsed_price = parse_price(price_token)
                if parsed_price is None:
                    continue
                if parsed_price < 50 or parsed_price > 500000:
                    continue
                inline_alias = f"{first}{second}"
                if not looks_like_alias(inline_alias, allow_numeric=True):
                    continue
                best_pair = (inline_alias, round(parsed_price, 2))
        return best_pair

    def _mixed_text_alias_price_pair(text: str) -> tuple[str, float] | None:
        """Extract one alias+price pair from mixed descriptive text.

        Some merged rows embed alias + description + price in a single cell
        (for example in the particulars column) while mapped alias/purchase
        columns are blank. This parser finds a valid alias group and the first
        valid price token that appears after it on the same line.
        """
        for line in split_cell_lines(text):
            tokens = line.replace(",", " ").split()
            if len(tokens) < 3:
                continue
            for idx in range(len(tokens) - 1):
                first, second = tokens[idx], tokens[idx + 1]
                if not (first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6):
                    continue
                alias_candidate = f"{first}{second}"
                if not looks_like_alias(alias_candidate, allow_numeric=True):
                    continue
                for token in tokens[idx + 2 :]:
                    parsed = parse_price(token)
                    if parsed is None:
                        continue
                    if 50 <= parsed <= 500000:
                        return alias_candidate, round(parsed, 2)
        return None

    def _alias_group_from_text(text: str) -> str | None:
        """Extract one numeric alias group from mixed text when present."""
        for line in split_cell_lines(text):
            tokens = line.replace(",", " ").split()
            if len(tokens) < 2:
                continue
            for idx in range(len(tokens) - 1):
                first, second = tokens[idx], tokens[idx + 1]
                if not (first.isdigit() and second.isdigit() and 4 <= len(first) <= 6 and 2 <= len(second) <= 6):
                    continue
                alias_candidate = f"{first}{second}"
                if looks_like_alias(alias_candidate, allow_numeric=True):
                    return alias_candidate
        return None

    current_like_purchases = {
        0.1,
        0.16,
        0.25,
        0.4,
        0.63,
        1.0,
        1.6,
        2.5,
        4.0,
        5.0,
        6.0,
        7.0,
        9.0,
        10.0,
        12.0,
        16.0,
        18.0,
        20.0,
        22.0,
        25.0,
        28.0,
        32.0,
        40.0,
        50.0,
        54.0,
        63.0,
        65.0,
        70.0,
        80.0,
        85.0,
        100.0,
        120.0,
        125.0,
        160.0,
        185.0,
        200.0,
        240.0,
        250.0,
        260.0,
        300.0,
        320.0,
        330.0,
        400.0,
        520.0,
        630.0,
        800.0,
        1000.0,
        1250.0,
        1600.0,
    }

    def _is_current_like_purchase(value: float) -> bool:
        return round(float(value), 2) in current_like_purchases

    def _looks_like_pack_token(text: str) -> bool:
        compact = " ".join(text.split()).strip()
        if not compact:
            return False
        if re.fullmatch(r"\d{1,2}", compact):
            return True
        if re.fullmatch(r"\d{1,2}\s*/\s*\d{1,2}", compact):
            return True
        return False

    def _extract_last_numeric_price(text: str) -> float | None:
        candidates: list[float] = []
        for token in re.findall(r"\d+(?:,\d{3})*(?:\.\d+)?", text):
            parsed = parse_price(token)
            if parsed is not None:
                candidates.append(parsed)
        if not candidates:
            return None
        return round(candidates[-1], 2)

    def _extract_with_mappings(
        data_rows: list[list[str]],
        mappings: list[dict[str, int]],
        scored_headers: list[str] | None = None,
        force_numeric_alias: bool = False,
    ) -> list[NormalizedRow]:
        normalized: list[NormalizedRow] = []
        purchase_usage_count: dict[int, int] = {}
        for _mapping in mappings:
            p_idx = _mapping["purchase"]
            purchase_usage_count[p_idx] = purchase_usage_count.get(p_idx, 0) + 1

        for row_idx, row in enumerate(data_rows):
            if not any(cell.strip() for cell in row):
                continue
            for mapping in mappings:
                alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
                price_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""
                particulars_raw = ""
                if "particulars" in mapping and mapping["particulars"] < len(row):
                    particulars_raw = row[mapping["particulars"]].strip()
                between_raw = ""
                left_col = min(mapping["alias"], mapping["purchase"])
                right_col = max(mapping["alias"], mapping["purchase"])
                if right_col - left_col > 1:
                    between_cells = [
                        row[col].strip()
                        for col in range(left_col + 1, right_col)
                        if col < len(row) and row[col].strip()
                    ]
                    between_raw = " ".join(between_cells).strip()
                mapped_pack_raw = ""
                if "pack" in mapping and mapping["pack"] < len(row):
                    mapped_pack_raw = row[mapping["pack"]].strip()
                shifted_pack_override = ""

                # If mapped purchase cell explicitly signals price-on-request,
                # treat MRP as unavailable and skip this row for this mapping.
                # Do not salvage purchase from neighboring nominal/current text.
                if price_raw and PRICE_ON_REQUEST_PATTERN.search(price_raw):
                    continue

                # Some merged matrices inside catalogs list electrical ratings
                # (for example "9 A", "265 A") in the mapped alias column,
                # while mapped purchase is actually kVAr/current, not MRP.
                # Skip such rows unless the alias cell has catalog-like grouping.
                if (
                    re.search(r"\b\d+(?:\.\d+)?\s*A\b", alias_raw, flags=re.IGNORECASE)
                    and not re.search(r"\d{4,6}[ \t]\d{2,6}", alias_raw)
                ):
                    continue

                # Dual-role merged columns can map alias and purchase to the
                # same index (for example "Cat.Nos ... MRP/Unit" in one cell).
                # Parse them as inline/stacked stream pairs instead of taking
                # one extracted alias and one extracted purchase from the same
                # raw cell, which can produce garbage pairings.
                if mapping["alias"] == mapping["purchase"]:
                    stream_pairs = _extract_row_pairs(alias_raw)
                    for stream_alias, stream_purchase in stream_pairs:
                        if not looks_like_alias(stream_alias, allow_numeric=True):
                            continue
                        pack = ""
                        if include_pack and "pack" in mapping and mapping["pack"] < len(row):
                            pack = clean_pack(row[mapping["pack"]])
                        normalized.append(
                            NormalizedRow(
                                particulars="",
                                alias=stream_alias,
                                purchase=round(stream_purchase, 2),
                                pack=pack,
                                source_page=page_number,
                            )
                        )
                    continue

                mixed_pair: tuple[str, float] | None = None
                if (not alias_raw or not price_raw):
                    mixed_pair = _mixed_text_alias_price_pair(alias_raw)
                    if mixed_pair is None and particulars_raw:
                        mixed_pair = _mixed_text_alias_price_pair(particulars_raw)

                purchase = parse_price(price_raw)

                # Some subsection rows collapse "No. of ways" + MRP into the
                # particulars cell (e.g. "- 18") and shift pack (e.g. "10")
                # into the mapped purchase column. Recover MRP from particulars
                # and preserve shifted pack when dedicated pack cell is missing.
                if (
                    purchase is not None
                    and _looks_like_pack_token(price_raw)
                    and not mapped_pack_raw
                    and (particulars_raw or between_raw)
                ):
                    shifted_purchase = None
                    for candidate_text in (particulars_raw, between_raw):
                        if not candidate_text:
                            continue
                        if not re.search(r"(?:^|\s)-\s*\d", candidate_text):
                            continue
                        shifted_purchase = _extract_last_numeric_price(candidate_text)
                        if shifted_purchase is not None:
                            break
                    if shifted_purchase is not None:
                        purchase = shifted_purchase
                        shifted_pack_override = clean_pack(price_raw)
                # Stacked purchase cells (e.g. "MRP\nPack\n4P-Cat.No") — try each
                # newline-separated line and take the first valid price (≥ 50).
                # This handles tables where MRP, pack, and 4P data are merged
                # into one column by Camelot but the first line is the actual MRP.
                if purchase is None and "\n" in price_raw:
                    stacked_candidates: list[float] = []
                    for _price_line in price_raw.split("\n"):
                        _p = parse_price(_price_line.strip())
                        if _p is not None and _p >= 50:
                            stacked_candidates.append(_p)
                    if stacked_candidates:
                        # In stacked mixed cells, candidates can include
                        # current ratings + MRP (+ pack). Prefer non-current-like
                        # values and then take the highest remaining candidate.
                        non_current = [v for v in stacked_candidates if not _is_current_like_purchase(v)]
                        if non_current:
                            purchase = max(non_current)
                        else:
                            purchase = max(stacked_candidates)

                # Additional generic guard for merged technical matrices where
                # alias cell may contain one catalog-looking token amidst
                # current/rating lines (e.g. "85 A\n4168 77\n..."), and
                # mapped purchase is actually a technical metric, not MRP.
                if (
                    purchase is not None
                    and purchase <= 500
                    and re.search(r"\b\d+(?:\.\d+)?\s*A\b", alias_raw, flags=re.IGNORECASE)
                    and re.search(r"\d{4,6}[ \t]\d{2,6}", alias_raw)
                    and re.search(r"[A-Za-z]", re.sub(r"\b\d+(?:\.\d+)?\s*A\b", "", alias_raw, flags=re.IGNORECASE))
                ):
                    continue
                particulars_alias = _alias_group_from_text(particulars_raw) if particulars_raw else None

                # Require line-level alias evidence in mapped alias columns.
                # This prevents multi-word description cells (for example feature text)
                # from being normalized into synthetic aliases.
                has_alias_line_evidence = looks_like_alias_line(alias_raw) or bool(
                    re.search(r"\d{3,6}[ \t]\d{2,6}", alias_raw)
                )
                if not has_alias_line_evidence:
                    alias_lines = split_cell_lines(alias_raw)
                    has_alias_line_evidence = any(looks_like_alias_line(line) for line in alias_lines)
                if not has_alias_line_evidence:
                    weak_alias = extract_alias(alias_raw, allow_numeric=True)
                    # Allow generic salvage when purchase is valid and particulars
                    # carries a split numeric alias group.
                    has_particulars_salvage = purchase is not None and particulars_alias is not None
                    # Without line-level alias evidence, accept only aliases with
                    # stronger numeric signatures (2+ consecutive digits).
                    # This rejects description-like tokens such as FRONT...-4P.
                    if mixed_pair is None and not has_particulars_salvage and not re.search(r"\d{2,}", weak_alias):
                        continue

                allow_numeric_alias = force_numeric_alias or (
                    scored_headers is not None
                    and mapping["alias"] < len(scored_headers)
                    and has_alias_header_evidence(scored_headers[mapping["alias"]])
                )

                alias = extract_alias(alias_raw, allow_numeric=allow_numeric_alias)

                # Some merged rows place alias text in particulars while keeping
                # purchase in the mapped purchase column.
                if not alias and purchase is not None and particulars_alias is not None:
                    alias = particulars_alias
                    allow_numeric_alias = True

                # In shifted rows, alias column can carry the previous row's
                # Cat.No while particulars starts with the actual Cat.No for
                # the current row (e.g. "5734 50" vs "5734 51 ...").
                # When particulars begins with a valid alias group and conflicts
                # with mapped alias, prefer particulars alias for this row.
                if (
                    alias
                    and purchase is not None
                    and particulars_alias is not None
                    and particulars_alias != alias
                    and re.match(r"^\s*\d{3,6}[ \t]\d{2,6}\b", particulars_raw)
                ):
                    alias = particulars_alias
                    allow_numeric_alias = True

                inline_pair = _inline_alias_price_pair(alias_raw)
                if inline_pair is not None:
                    inline_alias, inline_purchase = inline_pair
                    # Inline alias+price in the same cell is stronger evidence
                    # than a neighboring column in mixed/fragmented layouts.
                    if inline_alias == alias:
                        purchase = inline_purchase

                # When alias cell has stacked alias_group + MRP (e.g. "4242 01\n950"),
                # the cell-local MRP is more reliable than the mapped purchase column
                # which may hold pack or unrelated values in sub-section rows.
                purchase_has_header_evidence = (
                    scored_headers is not None
                    and mapping["purchase"] < len(scored_headers)
                    and has_purchase_header_evidence(scored_headers[mapping["purchase"]])
                )
                purchase_is_shared = purchase_usage_count.get(mapping["purchase"], 0) > 1
                if alias and (purchase is None or not purchase_has_header_evidence or purchase_is_shared):
                    stacked_pairs = _extract_row_pairs(alias_raw)
                    for stacked_alias, stacked_price in stacked_pairs:
                        if stacked_alias == alias:
                            purchase = stacked_price
                            break

                if purchase is None and (alias_raw or particulars_alias) and row_idx + 1 < len(data_rows):
                    next_row = data_rows[row_idx + 1]
                    next_alias_raw = next_row[mapping["alias"]].strip() if mapping["alias"] < len(next_row) else ""
                    next_price_raw = next_row[mapping["purchase"]].strip() if mapping["purchase"] < len(next_row) else ""
                    next_purchase = parse_price(next_price_raw)
                    next_pack_raw = ""
                    if "pack" in mapping and mapping["pack"] < len(next_row):
                        next_pack_raw = next_row[mapping["pack"]].strip()
                    if next_purchase is not None and not next_alias_raw and not next_pack_raw:
                        purchase = next_purchase

                if not alias and purchase is not None and particulars_alias is not None:
                    alias = particulars_alias
                    allow_numeric_alias = True

                # If mapped alias/purchase cells are blank or incomplete, try a
                # mixed-text salvage path from alias/raw particulars text.
                # Apply this only after continuation-row salvage was attempted.
                if mixed_pair is not None and purchase is None:
                    mixed_alias, mixed_purchase = mixed_pair
                    alias = mixed_alias
                    purchase = mixed_purchase
                    allow_numeric_alias = True

                if not looks_like_alias(alias, allow_numeric=allow_numeric_alias) or purchase is None:
                    continue

                pack = ""
                if include_pack and "pack" in mapping and mapping["pack"] < len(row):
                    pack = clean_pack(row[mapping["pack"]])
                if include_pack and not pack and shifted_pack_override:
                    pack = shifted_pack_override

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

        return normalized

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
        if inherited_mappings:
            inherited_rows = _extract_with_mappings(
                matrix,
                inherited_mappings,
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

        # Fall back to vertical table layouts
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
        if best_by_key:
            return list(best_by_key.values())

        # Final fallback: try stream with relaxed threshold for small stacked
        # tables (Cat.No/MRP/Pack in single cells) that don't reach 4 rows.
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

    normalized = _extract_with_mappings(data_rows, mappings, scored_headers=scored_headers)

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
        # When header-mapped extraction already succeeded, treat stream output
        # as supplemental and suppress current-like values that commonly leak
        # from nominal/current columns in mixed layouts.
        stream_rows = [r for r in stream_rows if not _is_current_like_purchase(r.purchase)]
        # Merge compact_vertical rows so that pack values from stacked cells
        # (e.g. "MRP\nPack" merged column) can upgrade pack-less header-path rows.
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
    if best_by_key:
        return list(best_by_key.values())

    # Final fallback: try stream with relaxed threshold for small stacked tables.
    relaxed_stream = extract_alias_price_stream_rows(
        matrix,
        page_number=page_number,
        include_particulars=include_particulars,
        include_pack=include_pack,
        min_rows=2,
    )
    return relaxed_stream
