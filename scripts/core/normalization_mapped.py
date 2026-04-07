"""Header-mapped row extraction for normalization."""

from __future__ import annotations

import re

from core.alias_price_stream import _extract_row_pairs
from core.header import has_alias_header_evidence, has_purchase_header_evidence
from core.models import NormalizedRow
from core.normalization_helpers import (
    alias_group_from_text,
    extract_inline_price_and_pack,
    extract_last_numeric_price,
    extract_trailing_text_price,
    inline_alias_price_pair,
    is_strong_alias_candidate,
    is_current_like_purchase,
    leading_alias_from_text,
    looks_like_pack_token,
    mixed_text_alias_price_pair,
)
from core.parsing import (
    PRICE_ON_REQUEST_PATTERN,
    clean_pack,
    extract_alias,
    looks_like_alias,
    looks_like_alias_line,
    parse_price,
)
from core.text_utils import fallback_particulars, split_cell_lines

PACK_CONTEXT_PATTERN = re.compile(
    r"\b(pack|packing|set(?:s)?|consisting|module(?:s)?|nos?\.?|pcs?\.?|pieces?)\b",
    flags=re.IGNORECASE,
)
SPACED_ALIAS_EVIDENCE_PATTERN = re.compile(r"\b\d{3,6}[ \t]\d{2,6}(?:[A-Za-z]{1,6})?\b")


def _choose_best_price(candidates: list[float]) -> float | None:
    if not candidates:
        return None
    non_current = [value for value in candidates if not is_current_like_purchase(value)]
    chosen = max(non_current) if non_current else max(candidates)
    return round(chosen, 2)


def _price_candidates_from_text(text: str) -> list[float]:
    candidates: list[float] = []
    for line in split_cell_lines(text):
        parsed = parse_price(line.strip())
        if parsed is not None and parsed >= 50:
            candidates.append(parsed)
    return candidates


def _strong_inline_alias(text: str, allow_numeric_alias: bool) -> str | None:
    alias = leading_alias_from_text(text, allow_numeric=allow_numeric_alias)
    if alias and is_strong_alias_candidate(alias, allow_numeric=allow_numeric_alias):
        if re.fullmatch(r"\d+\s*MODULES?", alias, flags=re.IGNORECASE):
            return None
        return alias
    return None


def _looks_like_technical_matrix_continuation(text: str) -> bool:
    lines = split_cell_lines(text)
    if not lines:
        return False

    first = lines[0]
    if re.fullmatch(r"\d+(?:\.\d+)?\s*A", first, flags=re.IGNORECASE):
        return True
    if re.match(r"^IEC\s*\d", first, flags=re.IGNORECASE):
        return True
    return False


def _numeric_stack_tokens(text: str) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    if re.search(r"[A-Za-z]", text):
        return tokens
    for line in split_cell_lines(text):
        compact = " ".join(line.split()).strip()
        if not compact:
            continue
        if not re.fullmatch(r"\d+(?:,\d{3})*(?:\.\d+)?", compact):
            continue
        parsed = parse_price(compact)
        if parsed is None or parsed < 50:
            continue
        tokens.append((compact, round(parsed, 2)))
    return tokens


def _infer_dual_role_numeric_order(data_rows: list[list[str]], mapping: dict[str, int]) -> str | None:
    """Infer ordering in numeric-only dual-role cells (price-first vs alias-first)."""
    if mapping.get("alias") != mapping.get("purchase"):
        return None

    alias_col = mapping["alias"]
    pack_col = mapping.get("pack")
    first_values: list[str] = []
    last_values: list[str] = []

    for row in data_rows:
        alias_raw = row[alias_col].strip() if alias_col < len(row) else ""
        if not alias_raw:
            continue
        if pack_col is not None:
            pack_raw = row[pack_col].strip() if pack_col < len(row) else ""
            if not (clean_pack(pack_raw) or looks_like_pack_token(pack_raw)):
                continue
        tokens = _numeric_stack_tokens(alias_raw)
        if len(tokens) < 2:
            continue
        first_values.append(tokens[0][0])
        last_values.append(tokens[-1][0])
        if len(first_values) >= 8:
            break

    if len(first_values) < 2:
        return None

    unique_first = len(set(first_values))
    unique_last = len(set(last_values))
    if unique_first < unique_last:
        return "price_first"
    if unique_last < unique_first:
        return "alias_first"
    return None


def _dual_role_numeric_stack_pair(
    alias_raw: str,
    order_hint: str | None,
) -> tuple[str, float] | None:
    if not order_hint:
        return None
    # Numeric stack orientation inference is intended for compact two-line
    # merged cells (price + alias or alias + price). If the cell already
    # carries a pack token, treat it as a richer mixed stack and let other
    # paths recover MRP to avoid current-rating leakage (e.g. 63, 4, 10794,
    # 1/5/60).
    if any(looks_like_pack_token(line) for line in split_cell_lines(alias_raw)):
        return None
    tokens = _numeric_stack_tokens(alias_raw)
    if len(tokens) != 2:
        return None

    if order_hint == "price_first":
        purchase = tokens[0][1]
        alias_token = tokens[-1][0]
    else:
        alias_token = tokens[0][0]
        purchase = tokens[-1][1]

    alias = extract_alias(alias_token, allow_numeric=True)
    if not looks_like_alias(alias, allow_numeric=True):
        return None
    return alias, round(purchase, 2)


def _is_split_row_price_candidate(
    row: list[str],
    mapping: dict[str, int],
    allow_numeric_alias: bool,
) -> bool:
    alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
    price_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""
    purchase = parse_price(price_raw)
    if purchase is None or purchase < 50 or is_current_like_purchase(purchase):
        return False
    if _strong_inline_alias(alias_raw, allow_numeric_alias) is not None:
        return False
    if _looks_like_technical_matrix_continuation(alias_raw):
        return False
    if re.search(r"[A-Za-z]", alias_raw):
        return False
    return True


def _infer_split_row_purchase_direction(
    data_rows: list[list[str]],
    mapping: dict[str, int],
    allow_numeric_alias: bool,
) -> str | None:
    """Infer whether split-row MRPs usually sit above or below alias rows."""
    if mapping.get("alias") == mapping.get("purchase"):
        return None

    prev_score = 0
    next_score = 0

    for row_idx, row in enumerate(data_rows):
        alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
        price_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""
        if not alias_raw or parse_price(price_raw) is not None:
            continue

        alias = _strong_inline_alias(alias_raw, allow_numeric_alias)
        if alias is None:
            continue

        prev_candidate = row_idx > 0 and _is_split_row_price_candidate(
            data_rows[row_idx - 1],
            mapping,
            allow_numeric_alias,
        )
        next_candidate = row_idx + 1 < len(data_rows) and _is_split_row_price_candidate(
            data_rows[row_idx + 1],
            mapping,
            allow_numeric_alias,
        )

        if prev_candidate and not next_candidate:
            prev_score += 2
        elif next_candidate and not prev_candidate:
            next_score += 2
        elif prev_candidate and next_candidate:
            if row_idx > 1 and _is_split_row_price_candidate(data_rows[row_idx - 2], mapping, allow_numeric_alias):
                prev_score += 1
            if row_idx + 2 < len(data_rows) and _is_split_row_price_candidate(
                data_rows[row_idx + 2],
                mapping,
                allow_numeric_alias,
            ):
                next_score += 1

    if prev_score >= 2 and prev_score > next_score:
        return "previous"
    if next_score >= 2 and next_score > prev_score:
        return "next"
    return None


def _has_alias_line_evidence(text: str) -> bool:
    if not text:
        return False
    lines = split_cell_lines(text)
    return any(looks_like_alias_line(line) for line in lines) or bool(
        SPACED_ALIAS_EVIDENCE_PATTERN.search(text)
    )


def _scan_neighbor_purchase(
    data_rows: list[list[str]],
    row_idx: int,
    mapping: dict[str, int],
    allow_numeric_alias: bool,
    preferred_direction: str | None,
) -> tuple[float, str] | None:
    """Find purchase from nearby rows while respecting local split-row structure."""
    direction_order = [-1, 1] if preferred_direction == "previous" else [1, -1]
    max_hops = 3

    for step in direction_order:
        for hop in range(1, max_hops + 1):
            neighbor_idx = row_idx + (step * hop)
            if neighbor_idx < 0 or neighbor_idx >= len(data_rows):
                break

            neighbor_row = data_rows[neighbor_idx]
            neighbor_alias_raw = (
                neighbor_row[mapping["alias"]].strip()
                if mapping["alias"] < len(neighbor_row)
                else ""
            )
            neighbor_price_raw = (
                neighbor_row[mapping["purchase"]].strip()
                if mapping["purchase"] < len(neighbor_row)
                else ""
            )

            if _strong_inline_alias(neighbor_alias_raw, allow_numeric_alias) is not None:
                break
            if _has_alias_line_evidence(neighbor_alias_raw):
                break
            if _looks_like_technical_matrix_continuation(neighbor_alias_raw):
                continue

            candidate = parse_price(neighbor_price_raw)
            if candidate is None or candidate < 50 or is_current_like_purchase(candidate):
                continue

            neighbor_pack = ""
            if "pack" in mapping and mapping["pack"] < len(neighbor_row):
                neighbor_pack = clean_pack(neighbor_row[mapping["pack"]])

            return round(candidate, 2), neighbor_pack

    return None


def extract_with_mappings(
    data_rows: list[list[str]],
    mappings: list[dict[str, int]],
    page_number: int,
    include_particulars: bool = False,
    include_pack: bool = False,
    scored_headers: list[str] | None = None,
    force_numeric_alias: bool = False,
) -> list[NormalizedRow]:
    """Extract normalized rows using explicit header-derived column mappings."""
    normalized: list[NormalizedRow] = []
    # Aliases for which the mapped purchase column contained an explicit
    # price-on-request marker (e.g. 'n', '■').  Continuation rows that share
    # the same alias but have an *empty* purchase cell must not fall back to
    # mining trailing numbers from description text (e.g. RAL color codes).
    por_aliases: set[str] = set()
    purchase_usage_count: dict[int, int] = {}
    dual_role_order_hint: dict[int, str | None] = {}
    allow_numeric_alias_by_mapping: dict[int, bool] = {}
    purchase_header_evidence_by_mapping: dict[int, bool] = {}
    split_row_purchase_direction: dict[int, str | None] = {}
    for mapping in mappings:
        p_idx = mapping["purchase"]
        purchase_usage_count[p_idx] = purchase_usage_count.get(p_idx, 0) + 1
    for idx, mapping in enumerate(mappings):
        allow_numeric_alias_by_mapping[idx] = force_numeric_alias or (
            scored_headers is not None
            and mapping["alias"] < len(scored_headers)
            and has_alias_header_evidence(scored_headers[mapping["alias"]])
        )
        purchase_header_evidence_by_mapping[idx] = (
            scored_headers is not None
            and mapping["purchase"] < len(scored_headers)
            and has_purchase_header_evidence(scored_headers[mapping["purchase"]])
        )
        dual_role_order_hint[idx] = _infer_dual_role_numeric_order(data_rows, mapping)
        split_row_purchase_direction[idx] = _infer_split_row_purchase_direction(
            data_rows,
            mapping,
            allow_numeric_alias_by_mapping[idx],
        )

    for row_idx, row in enumerate(data_rows):
        if not any(cell.strip() for cell in row):
            continue
        for mapping_idx, mapping in enumerate(mappings):
            alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
            price_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""
            particulars_raw = ""
            if "particulars" in mapping and mapping["particulars"] < len(row):
                particulars_raw = row[mapping["particulars"]].strip()

            allow_numeric_alias = allow_numeric_alias_by_mapping[mapping_idx]

            purchase_header_evidence_for_mapping = purchase_header_evidence_by_mapping[mapping_idx]

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

            # Standalone lowercase 'n' is the price-on-request marker used by
            # some catalog PDFs (footnote: "n Price available on request.").
            # Treat it the same way as the special-character POR markers above.
            _is_por = price_raw and (
                PRICE_ON_REQUEST_PATTERN.search(price_raw)
                or price_raw.strip().lower() == "n"
            )
            if _is_por:
                # Record the alias so continuation rows for the same product
                # (which may have an empty MRP cell) are also suppressed.
                if alias_raw:
                    _por_candidate = extract_alias(alias_raw, allow_numeric=True)
                    if _por_candidate:
                        por_aliases.add(_por_candidate)
                continue

            # If a mapped purchase cell contains alphabetic description text
            # and lacks purchase-header evidence, do not parse numerics from it.
            if (
                price_raw
                and re.search(r"[A-Za-z]", price_raw)
                and not purchase_header_evidence_for_mapping
            ):
                continue

            if (
                re.search(r"\b\d+(?:\.\d+)?\s*A\b", alias_raw, flags=re.IGNORECASE)
                and not re.search(r"\d{4,6}[ \t]\d{2,6}", alias_raw)
                and _strong_inline_alias(alias_raw, allow_numeric_alias) is None
            ):
                continue

            if mapping["alias"] == mapping["purchase"]:
                stream_pairs = _extract_row_pairs(alias_raw)
                inferred_numeric_pair = _dual_role_numeric_stack_pair(
                    alias_raw,
                    dual_role_order_hint.get(mapping_idx),
                )

                # In some shifted blocks, the dual-role cell starts with a
                # standalone MRP line that belongs to a neighboring alias in
                # the immediate left column (e.g. `4210 60 | 42400\n4210 61...`).
                # Recover that pair and prevent the in-cell alias from taking
                # the shifted leading price.
                shifted_neighbor_pair: tuple[str, float] | None = None
                alias_col = mapping["alias"]
                leading_lines = split_cell_lines(alias_raw)
                leading_price = parse_price(leading_lines[0]) if leading_lines else None
                if leading_price is not None and leading_price >= 50:
                    for left_col in (alias_col - 1, alias_col - 2):
                        if left_col < 0 or left_col >= len(row):
                            continue
                        left_raw = row[left_col].strip()
                        if not left_raw:
                            continue
                        if not re.fullmatch(r"\d{3,6}[ \t]\d{2,6}", left_raw):
                            continue
                        left_alias = extract_alias(left_raw, allow_numeric=True)
                        if not looks_like_alias(left_alias, allow_numeric=True):
                            continue
                        shifted_neighbor_pair = (left_alias, round(leading_price, 2))
                        break

                if shifted_neighbor_pair is not None:
                    shifted_alias, shifted_purchase = shifted_neighbor_pair
                    stream_pairs_suppressed: list[tuple[str, float]] = []
                    dropped_leading = False
                    for pair_alias, pair_purchase in stream_pairs:
                        if not dropped_leading and round(pair_purchase, 2) == shifted_purchase:
                            dropped_leading = True
                            continue
                        stream_pairs_suppressed.append((pair_alias, pair_purchase))
                    stream_pairs = stream_pairs_suppressed

                    normalized.append(
                        NormalizedRow(
                            particulars="",
                            alias=shifted_alias,
                            purchase=shifted_purchase,
                            pack="",
                            source_page=page_number,
                        )
                    )

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

                if (
                    inferred_numeric_pair is not None
                    and not any(
                        pair_alias == inferred_numeric_pair[0]
                        and round(pair_purchase, 2) == inferred_numeric_pair[1]
                        for pair_alias, pair_purchase in stream_pairs
                    )
                ):
                    pack = ""
                    if include_pack and "pack" in mapping and mapping["pack"] < len(row):
                        pack = clean_pack(row[mapping["pack"]])
                    normalized.append(
                        NormalizedRow(
                            particulars="",
                            alias=inferred_numeric_pair[0],
                            purchase=inferred_numeric_pair[1],
                            pack=pack,
                            source_page=page_number,
                        )
                    )

                same_cell_alias = _strong_inline_alias(alias_raw, allow_numeric_alias)
                same_cell_purchase = _choose_best_price(_price_candidates_from_text(alias_raw))
                if (
                    same_cell_alias
                    and same_cell_purchase is not None
                    and inferred_numeric_pair is None
                    and not any(
                        pair_alias == same_cell_alias and round(pair_purchase, 2) == same_cell_purchase
                        for pair_alias, pair_purchase in stream_pairs
                    )
                ):
                    pack = ""
                    if include_pack and "pack" in mapping and mapping["pack"] < len(row):
                        pack = clean_pack(row[mapping["pack"]])
                    normalized.append(
                        NormalizedRow(
                            particulars="",
                            alias=same_cell_alias,
                            purchase=same_cell_purchase,
                            pack=pack,
                            source_page=page_number,
                        )
                    )

                if same_cell_alias and same_cell_purchase is None:
                    neighbor_purchase = None
                    alias_group_pattern = re.compile(r"\d{3,6}[ \t]\d{2,6}")

                    def _scan_direction(step: int) -> float | None:
                        max_hops = 4
                        for hop in range(1, max_hops + 1):
                            neighbor_idx = row_idx + hop * step
                            if neighbor_idx < 0 or neighbor_idx >= len(data_rows):
                                break
                            neighbor_row = data_rows[neighbor_idx]
                            neighbor_raw = (
                                neighbor_row[mapping["alias"]].strip()
                                if mapping["alias"] < len(neighbor_row)
                                else ""
                            )
                            if not neighbor_raw:
                                continue

                            neighbor_strong_alias = _strong_inline_alias(neighbor_raw, allow_numeric_alias)
                            if neighbor_strong_alias and (
                                alias_group_pattern.search(neighbor_raw)
                                or re.search(r"[A-Za-z]", neighbor_raw)
                            ):
                                # Hit another strong catalog row in this
                                # direction; do not cross item boundaries.
                                break

                            candidate = _choose_best_price(_price_candidates_from_text(neighbor_raw))
                            if candidate is not None:
                                return candidate
                        return None

                    neighbor_purchase = _scan_direction(1)
                    if neighbor_purchase is None:
                        neighbor_purchase = _scan_direction(-1)

                    if neighbor_purchase is not None:
                        pack = ""
                        if include_pack and "pack" in mapping and mapping["pack"] < len(row):
                            pack = clean_pack(row[mapping["pack"]])
                        normalized.append(
                            NormalizedRow(
                                particulars="",
                                alias=same_cell_alias,
                                purchase=neighbor_purchase,
                                pack=pack,
                                source_page=page_number,
                            )
                        )
                continue

            mixed_pair: tuple[str, float] | None = None
            if not alias_raw or not price_raw:
                mixed_pair = mixed_text_alias_price_pair(alias_raw, looks_like_alias)
                if mixed_pair is None and particulars_raw:
                    mixed_pair = mixed_text_alias_price_pair(particulars_raw, looks_like_alias)

            purchase = parse_price(price_raw)
            if purchase is None and price_raw:
                inline_cell_purchase, inline_cell_pack = extract_inline_price_and_pack(price_raw)
                if inline_cell_purchase is not None:
                    purchase = inline_cell_purchase
                    if not mapped_pack_raw and inline_cell_pack:
                        shifted_pack_override = inline_cell_pack

            # Some shifted rows place "MRP pack" in the mapped pack column
            # while the mapped purchase column is blank. Recover MRP from
            # that inline token stream instead of dropping the row.
            if purchase is None and not price_raw and mapped_pack_raw:
                inline_pack_purchase, inline_pack_value = extract_inline_price_and_pack(mapped_pack_raw)
                if inline_pack_purchase is not None:
                    purchase = inline_pack_purchase
                    if inline_pack_value:
                        shifted_pack_override = inline_pack_value

            if (purchase is None and not price_raw) or (
                purchase is not None and not purchase_header_evidence_for_mapping
            ):
                nearby_candidates: list[float] = []
                purchase_is_right_of_alias = mapping["purchase"] >= mapping["alias"]
                for col_idx, cell in enumerate(row):
                    if col_idx in {mapping["alias"], mapping["purchase"]}:
                        continue
                    if "pack" in mapping and col_idx == mapping["pack"]:
                        continue
                    # Restrict nearby fallback to the same side of the alias
                    # as the mapped purchase column. This avoids leaking MRP
                    # from parallel side-by-side table blocks.
                    if purchase_is_right_of_alias and col_idx < mapping["alias"]:
                        continue
                    if not purchase_is_right_of_alias and col_idx > mapping["alias"]:
                        continue
                    if (
                        scored_headers is None
                        or col_idx >= len(scored_headers)
                        or not has_purchase_header_evidence(scored_headers[col_idx])
                    ):
                        continue
                    if abs(col_idx - mapping["alias"]) > 3:
                        continue
                    cell_raw = cell.strip()
                    if not cell_raw:
                        continue
                    parsed_nearby = parse_price(cell_raw)
                    if parsed_nearby is None or parsed_nearby < 50:
                        continue
                    nearby_candidates.append(parsed_nearby)
                if nearby_candidates:
                    non_current = [v for v in nearby_candidates if not is_current_like_purchase(v)]
                    candidate = max(non_current) if non_current else None
                    if candidate is not None and (purchase is None or candidate > purchase):
                        purchase = candidate

            if (
                purchase is not None
                and looks_like_pack_token(price_raw)
                and not mapped_pack_raw
                and (particulars_raw or between_raw)
            ):
                shifted_purchase = None
                for candidate_text in (particulars_raw, between_raw):
                    if not candidate_text:
                        continue
                    if not re.search(r"(?:^|\s)-\s*\d", candidate_text):
                        continue
                    shifted_purchase = extract_last_numeric_price(candidate_text)
                    if shifted_purchase is not None:
                        break
                if shifted_purchase is not None:
                    purchase = shifted_purchase
                    shifted_pack_override = clean_pack(price_raw)

            if purchase is None and "\n" in price_raw:
                stacked_candidates: list[float] = []
                for price_line in price_raw.split("\n"):
                    parsed_line = parse_price(price_line.strip())
                    if parsed_line is not None and parsed_line >= 50:
                        stacked_candidates.append(parsed_line)
                if stacked_candidates:
                    non_current = [v for v in stacked_candidates if not is_current_like_purchase(v)]
                    purchase = max(non_current) if non_current else max(stacked_candidates)

            if (
                purchase is not None
                and purchase <= 500
                and re.search(r"\b\d+(?:\.\d+)?\s*A\b", alias_raw, flags=re.IGNORECASE)
                and re.search(r"\d{4,6}[ \t]\d{2,6}", alias_raw)
                and re.search(r"[A-Za-z]", re.sub(r"\b\d+(?:\.\d+)?\s*A\b", "", alias_raw, flags=re.IGNORECASE))
            ):
                continue

            particulars_leading_alias = (
                leading_alias_from_text(particulars_raw, allow_numeric=allow_numeric_alias) if particulars_raw else None
            )
            if particulars_leading_alias and not is_strong_alias_candidate(
                particulars_leading_alias,
                allow_numeric=allow_numeric_alias,
            ):
                particulars_leading_alias = None
            particulars_alias = particulars_leading_alias
            if particulars_alias is None and particulars_raw:
                particulars_alias = alias_group_from_text(particulars_raw, looks_like_alias)

            has_alias_line_evidence = looks_like_alias_line(alias_raw) or bool(
                SPACED_ALIAS_EVIDENCE_PATTERN.search(alias_raw)
            )
            if not has_alias_line_evidence and alias_raw:
                numeric_alias_line = extract_alias(alias_raw, allow_numeric=True)
                if looks_like_alias(numeric_alias_line, allow_numeric=True):
                    has_alias_line_evidence = True
            leading_inline_alias = _strong_inline_alias(alias_raw, allow_numeric_alias) if alias_raw else None
            if not has_alias_line_evidence:
                alias_lines = split_cell_lines(alias_raw)
                has_alias_line_evidence = any(looks_like_alias_line(line) for line in alias_lines)
            if not has_alias_line_evidence:
                weak_alias = extract_alias(alias_raw, allow_numeric=True)
                has_particulars_salvage = purchase is not None and particulars_alias is not None
                has_particulars_alias_candidate = particulars_alias is not None
                if (
                    mixed_pair is None
                    and not has_particulars_salvage
                    and not has_particulars_alias_candidate
                    and leading_inline_alias is None
                ):
                    # Guard against section headings in mapped alias columns
                    # (for example "Double pole 240V") being promoted into
                    # synthetic aliases when purchase is salvaged nearby.
                    multiword_alias_text = bool(re.search(r"\s", alias_raw.strip()))
                    has_split_numeric_group = bool(SPACED_ALIAS_EVIDENCE_PATTERN.search(alias_raw))
                    if multiword_alias_text and not has_split_numeric_group:
                        continue
                    if not is_strong_alias_candidate(weak_alias, allow_numeric=True):
                        continue

            alias = extract_alias(alias_raw, allow_numeric=allow_numeric_alias)

            if not alias and purchase is not None and particulars_alias is not None:
                alias = particulars_alias
                allow_numeric_alias = True

            if (
                alias
                and purchase is not None
                and particulars_leading_alias is not None
                and particulars_leading_alias != alias
            ):
                prev_alias = ""
                if row_idx > 0:
                    prev_row = data_rows[row_idx - 1]
                    prev_alias_raw = prev_row[mapping["alias"]].strip() if mapping["alias"] < len(prev_row) else ""
                    prev_alias = extract_alias(prev_alias_raw, allow_numeric=allow_numeric_alias)

                # Only let a leading particulars alias override a mapped alias
                # when the alias column is weak/blank or clearly stale from the
                # prior row. This preserves true Cat.Nos while still fixing
                # shifted rows where Camelot leaves the previous Cat.No in the
                # alias column and the new one starts the description.
                alias_is_stale_repeat = bool(prev_alias and prev_alias == alias)
                particulars_digit_count = sum(ch.isdigit() for ch in particulars_leading_alias)
                alias_digit_count = sum(ch.isdigit() for ch in alias)
                particulars_is_at_least_as_catalog_dense = particulars_digit_count >= alias_digit_count
                if (
                    not has_alias_line_evidence
                    or alias_is_stale_repeat
                    or particulars_is_at_least_as_catalog_dense
                ):
                    alias = particulars_leading_alias
                    allow_numeric_alias = True

            inline_pair = inline_alias_price_pair(alias_raw, looks_like_alias)
            if inline_pair is not None:
                inline_alias, inline_purchase = inline_pair
                if inline_alias == alias:
                    purchase = inline_purchase

            # In dual-block shifted layouts, a mapped purchase cell can carry
            # the next block's alias+price stack. If alias cell itself contains
            # multiple inline alias-price pairs, prefer the pair aligned with
            # this alias over the shifted mapped purchase.
            if (
                alias
                and purchase is not None
                and re.search(r"\b\d{3,6}[ \t]\d{2,6}\b", price_raw)
            ):
                alias_cell_pairs = _extract_row_pairs(alias_raw)
                if len(alias_cell_pairs) >= 2:
                    for pair_alias, pair_purchase in alias_cell_pairs:
                        if pair_alias == alias:
                            purchase = pair_purchase
                            break

            purchase_has_header_evidence = purchase_header_evidence_for_mapping
            purchase_is_shared = purchase_usage_count.get(mapping["purchase"], 0) > 1
            if alias and (purchase is None or not purchase_has_header_evidence or purchase_is_shared):
                stacked_pairs = _extract_row_pairs(alias_raw)
                for stacked_alias, stacked_price in stacked_pairs:
                    if stacked_alias == alias:
                        purchase = stacked_price
                        break

            salvage_alias = alias or particulars_alias
            alias_is_strong = (
                is_strong_alias_candidate(salvage_alias, allow_numeric=allow_numeric_alias)
                if salvage_alias
                else False
            )

            # Locality-aware purchase salvage for split-row layouts:
            # use inferred block orientation first and stop at alias boundaries.
            if purchase is None and alias_is_strong and (alias_raw or particulars_alias):
                neighbor_salvage = _scan_neighbor_purchase(
                    data_rows,
                    row_idx,
                    mapping,
                    allow_numeric_alias,
                    split_row_purchase_direction.get(mapping_idx),
                )
                if neighbor_salvage is not None:
                    purchase, neighbor_pack = neighbor_salvage
                    if not shifted_pack_override and neighbor_pack:
                        shifted_pack_override = neighbor_pack

            # Secondary fallback for the variant where the previous alias cell
            # itself is a numeric stack and can hold MRP lines.
            if purchase is None and alias_is_strong and alias and row_idx > 0:
                prev_row = data_rows[row_idx - 1]
                prev_alias_raw = prev_row[mapping["alias"]].strip() if mapping["alias"] < len(prev_row) else ""
                if prev_alias_raw:
                    prev_lines = split_cell_lines(prev_alias_raw)
                    prev_has_alias = _has_alias_line_evidence(prev_alias_raw)
                    prev_has_text = bool(re.search(r"[A-Za-z]", prev_alias_raw))
                    if not prev_has_alias and not prev_has_text:
                        prev_candidates: list[float] = []
                        for line in prev_lines:
                            parsed_prev = parse_price(line.strip())
                            if parsed_prev is not None and parsed_prev >= 50:
                                prev_candidates.append(parsed_prev)
                        if prev_candidates:
                            prev_non_current = [v for v in prev_candidates if not is_current_like_purchase(v)]
                            if prev_non_current:
                                purchase = max(prev_non_current)

            if purchase is None and (alias or particulars_alias) and (particulars_raw or between_raw):
                for candidate_text in (particulars_raw, between_raw):
                    if not candidate_text:
                        continue
                    inline_purchase_from_text, inline_pack_from_text = extract_inline_price_and_pack(candidate_text)
                    if inline_purchase_from_text is None:
                        continue
                    purchase = inline_purchase_from_text
                    if not mapped_pack_raw and inline_pack_from_text and not shifted_pack_override:
                        shifted_pack_override = inline_pack_from_text
                    break

            pack_context_in_text = any(PACK_CONTEXT_PATTERN.search(text or "") for text in (particulars_raw, between_raw))
            can_salvage_trailing_text_price = not (
                purchase_header_evidence_for_mapping and not price_raw and bool(mapped_pack_raw) and pack_context_in_text
            )
            # Do not mine description text for trailing numbers when the alias
            # was previously seen with an explicit POR marker.  Continuation
            # rows (empty MRP cell) would otherwise harvest values like "RAL
            # 4005"색 color-codes or specification suffixes as fake prices.
            alias_is_por = bool(alias and alias in por_aliases) or bool(
                particulars_alias and particulars_alias in por_aliases
            )
            if (
                purchase is None
                and (alias or particulars_alias)
                and can_salvage_trailing_text_price
                and not alias_is_por
                and (particulars_raw or between_raw)
            ):
                for candidate_text in (particulars_raw, between_raw):
                    if not candidate_text:
                        continue
                    trailing_particulars_price = extract_trailing_text_price(candidate_text)
                    if trailing_particulars_price is not None:
                        purchase = trailing_particulars_price
                        break

            if (
                purchase is not None
                and purchase < 50
                and looks_like_pack_token(price_raw)
                and (alias or particulars_alias)
                and (particulars_raw or between_raw)
            ):
                for candidate_text in (particulars_raw, between_raw):
                    if not candidate_text:
                        continue
                    trailing_particulars_price = extract_trailing_text_price(candidate_text)
                    if trailing_particulars_price is not None:
                        purchase = trailing_particulars_price
                        shifted_pack_override = clean_pack(price_raw)
                        break

            if not alias and purchase is not None and particulars_alias is not None:
                alias = particulars_alias
                allow_numeric_alias = True

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

            alias_candidates: list[str] = [alias]
            raw_alias_for_line_expansion = (
                extract_alias(alias_raw, allow_numeric=allow_numeric_alias) if alias_raw else ""
            )
            allow_line_alias_expansion = bool(alias_raw) and raw_alias_for_line_expansion == alias

            if allow_line_alias_expansion:
                for alias_line in split_cell_lines(alias_raw):
                    line_alias = extract_alias(alias_line, allow_numeric=allow_numeric_alias)
                    if not looks_like_alias(line_alias, allow_numeric=allow_numeric_alias):
                        continue
                    if not is_strong_alias_candidate(line_alias, allow_numeric=allow_numeric_alias):
                        continue
                    if (
                        alias.isdigit()
                        and line_alias.isdigit()
                        and len(line_alias) < len(alias)
                        and alias.endswith(line_alias)
                    ):
                        continue
                    if line_alias not in alias_candidates:
                        alias_candidates.append(line_alias)

            for alias_candidate in alias_candidates:
                normalized.append(
                    NormalizedRow(
                        particulars=particulars,
                        alias=alias_candidate,
                        purchase=round(purchase, 2),
                        pack=pack,
                        source_page=page_number,
                    )
                )

    return normalized
