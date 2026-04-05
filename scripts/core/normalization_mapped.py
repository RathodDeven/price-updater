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
    purchase_usage_count: dict[int, int] = {}
    for mapping in mappings:
        p_idx = mapping["purchase"]
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

            allow_numeric_alias = force_numeric_alias or (
                scored_headers is not None
                and mapping["alias"] < len(scored_headers)
                and has_alias_header_evidence(scored_headers[mapping["alias"]])
            )

            purchase_header_evidence_for_mapping = (
                scored_headers is not None
                and mapping["purchase"] < len(scored_headers)
                and has_purchase_header_evidence(scored_headers[mapping["purchase"]])
            )

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

            if price_raw and PRICE_ON_REQUEST_PATTERN.search(price_raw):
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

                same_cell_alias = _strong_inline_alias(alias_raw, allow_numeric_alias)
                same_cell_purchase = _choose_best_price(_price_candidates_from_text(alias_raw))
                if (
                    same_cell_alias
                    and same_cell_purchase is not None
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
                    for neighbor_idx in (row_idx - 1, row_idx + 1):
                        if neighbor_idx < 0 or neighbor_idx >= len(data_rows):
                            continue
                        neighbor_row = data_rows[neighbor_idx]
                        neighbor_raw = (
                            neighbor_row[mapping["alias"]].strip()
                            if mapping["alias"] < len(neighbor_row)
                            else ""
                        )
                        if not neighbor_raw:
                            continue
                        if _strong_inline_alias(neighbor_raw, allow_numeric_alias):
                            continue
                        neighbor_purchase = _choose_best_price(_price_candidates_from_text(neighbor_raw))
                        if neighbor_purchase is not None:
                            break

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

            if (purchase is None and not price_raw) or (
                purchase is not None and not purchase_header_evidence_for_mapping
            ):
                nearby_candidates: list[float] = []
                for col_idx, cell in enumerate(row):
                    if col_idx in {mapping["alias"], mapping["purchase"]}:
                        continue
                    if "pack" in mapping and col_idx == mapping["pack"]:
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
            leading_inline_alias = _strong_inline_alias(alias_raw, allow_numeric_alias) if alias_raw else None
            if not has_alias_line_evidence:
                alias_lines = split_cell_lines(alias_raw)
                has_alias_line_evidence = any(looks_like_alias_line(line) for line in alias_lines)
            if not has_alias_line_evidence:
                weak_alias = extract_alias(alias_raw, allow_numeric=True)
                has_particulars_salvage = purchase is not None and particulars_alias is not None
                if mixed_pair is None and not has_particulars_salvage and leading_inline_alias is None:
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

            if purchase is None and alias_is_strong and (alias_raw or particulars_alias) and row_idx + 1 < len(data_rows):
                next_row = data_rows[row_idx + 1]
                next_alias_raw = next_row[mapping["alias"]].strip() if mapping["alias"] < len(next_row) else ""
                next_price_raw = next_row[mapping["purchase"]].strip() if mapping["purchase"] < len(next_row) else ""
                next_purchase = parse_price(next_price_raw)
                next_pack_raw = ""
                if "pack" in mapping and mapping["pack"] < len(next_row):
                    next_pack_raw = next_row[mapping["pack"]].strip()
                next_has_strong_alias = _strong_inline_alias(next_alias_raw, allow_numeric_alias) is not None
                next_is_technical = _looks_like_technical_matrix_continuation(next_alias_raw)
                if (
                    next_purchase is not None
                    and not next_has_strong_alias
                    and not next_is_technical
                    and not is_current_like_purchase(next_purchase)
                ):
                    purchase = next_purchase
                    if not shifted_pack_override and clean_pack(next_pack_raw):
                        shifted_pack_override = clean_pack(next_pack_raw)

            # Variant of split-row layouts: alias appears on current row while
            # MRP is stacked in the previous row in the same alias column.
            if purchase is None and alias_is_strong and alias and row_idx > 0:
                prev_row = data_rows[row_idx - 1]
                prev_alias_raw = prev_row[mapping["alias"]].strip() if mapping["alias"] < len(prev_row) else ""
                if prev_alias_raw:
                    prev_lines = split_cell_lines(prev_alias_raw)
                    prev_has_alias = any(looks_like_alias_line(line) for line in prev_lines) or bool(
                        SPACED_ALIAS_EVIDENCE_PATTERN.search(prev_alias_raw)
                    )
                    prev_has_text = bool(re.search(r"[A-Za-z]", prev_alias_raw))
                    if purchase is None and not prev_has_alias and not prev_has_text:
                        prev_candidates: list[float] = []
                        for line in prev_lines:
                            parsed_prev = parse_price(line.strip())
                            if parsed_prev is not None and parsed_prev >= 50:
                                prev_candidates.append(parsed_prev)
                        if prev_candidates:
                            prev_non_current = [v for v in prev_candidates if not is_current_like_purchase(v)]
                            if prev_non_current:
                                purchase = max(prev_non_current)
                    if purchase is None and not prev_has_alias:
                        prev_price_raw = (
                            prev_row[mapping["purchase"]].strip() if mapping["purchase"] < len(prev_row) else ""
                        )
                        prev_price = parse_price(prev_price_raw)
                        if (
                            prev_price is not None
                            and prev_price >= 50
                            and not _looks_like_technical_matrix_continuation(prev_alias_raw)
                        ):
                            purchase = prev_price
                if purchase is None and not prev_alias_raw:
                    prev_price_raw = prev_row[mapping["purchase"]].strip() if mapping["purchase"] < len(prev_row) else ""
                    prev_price = parse_price(prev_price_raw)
                    if prev_price is not None and prev_price >= 50 and not is_current_like_purchase(prev_price):
                        purchase = prev_price

            if purchase is None and alias and (particulars_raw or between_raw):
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
            if purchase is None and alias and can_salvage_trailing_text_price and (particulars_raw or between_raw):
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
                and alias
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

            alias_candidates: list[str] = []
            for alias_line in split_cell_lines(alias_raw):
                line_alias = extract_alias(alias_line, allow_numeric=allow_numeric_alias)
                if not looks_like_alias(line_alias, allow_numeric=allow_numeric_alias):
                    continue
                if not is_strong_alias_candidate(line_alias, allow_numeric=allow_numeric_alias):
                    continue
                if line_alias not in alias_candidates:
                    alias_candidates.append(line_alias)
            if not alias_candidates:
                alias_candidates = [alias]
            elif alias not in alias_candidates:
                alias_candidates.insert(0, alias)

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
