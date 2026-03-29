"""Camelot-based table extraction backend (free, for native PDF tables)."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import logging
from pathlib import Path
import re
from typing import Any

import camelot
import fitz

from .base import TableExtractor


logger = logging.getLogger(__name__)


HEADER_GROUP_MARKERS = {
    "alias": {"cat nos", "cat nos.", "cat.nos", "cat.nos.", "reference", "reference no", "ref no", "item code"},
    "particulars": {"description", "particulars", "item description"},
    "purchase": {"mrp", "mrp*", "unit", "unit price", "price"},
    "pack": {"pack", "std pkg", "std. pkg", "nos"},
}


def _normalize_marker(text: str) -> str:
    lowered = str(text).lower().replace("₹", " inr ")
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return " ".join(cleaned.split()).strip()


def _has_group_marker(text: str, role: str) -> bool:
    normalized = _normalize_marker(text)
    if not normalized:
        return False
    padded = f" {normalized} "
    markers = {
        _normalize_marker(marker)
        for marker in HEADER_GROUP_MARKERS.get(role, set())
        if _normalize_marker(marker)
    }
    return any(f" {marker} " in padded for marker in markers)


def _find_repeated_header_groups(page: Any) -> list[dict[str, float]]:
    words = page.get_text("words")
    if not words:
        return []

    # Ignore footer/legal notes where Cat.Nos often appears in disclaimers.
    footer_limit = page.rect.height * 0.9
    header_words = []
    for word in words:
        x0, y0, x1, y1, text, *_ = word
        if y0 > footer_limit:
            continue
        normalized = _normalize_marker(text)
        if not normalized:
            continue
        header_words.append({"x0": x0, "x1": x1, "y0": y0, "text": text, "normalized": normalized})

    alias_words = [w for w in header_words if _has_group_marker(w["text"], "alias")]
    if len(alias_words) < 2:
        return []

    groups: list[dict[str, float]] = []
    sorted_alias = sorted(alias_words, key=lambda word: (float(word["y0"]), float(word["x0"])))
    for alias_word in sorted_alias:
        region_words = [
            word for word in header_words
            if abs(float(word["y0"]) - float(alias_word["y0"])) <= 22
            and float(alias_word["x0"]) - 10 <= float(word["x0"]) <= float(alias_word["x0"]) + 260
        ]
        role_positions: dict[str, float] = {
            "alias": float(alias_word["x0"]),
            "y": float(alias_word["y0"]),
        }
        for role in ("particulars", "purchase", "pack"):
            candidates = [
                float(word["x0"])
                for word in region_words
                if _has_group_marker(word["text"], role)
                and float(word["x0"]) > float(alias_word["x0"])
            ]
            if candidates:
                role_positions[role] = min(candidates)

        # particulars is optional for compact price grids (e.g. 3P/4P tables)
        if "purchase" not in role_positions or "pack" not in role_positions:
            continue

        if any(
            abs(role_positions["alias"] - existing["alias"]) <= 8
            and abs(role_positions["y"] - existing["y"]) <= 8
            for existing in groups
        ):
            continue
        groups.append(role_positions)

    if len(groups) < 2:
        return []

    # Two-column spreads should have header groups on roughly the same row.
    y_values = [g["y"] for g in groups]
    if max(y_values) - min(y_values) > 40:
        return []

    return groups


def _extract_repeated_header_regions(pdf_path: Path, page_num: int) -> list[list[list[str]]]:
    doc = fitz.open(pdf_path)
    try:
        page: Any = doc[page_num]
        groups = _find_repeated_header_groups(page)
        if len(groups) < 2:
            return []

        width = page.rect.width
        height = page.rect.height
        result: list[list[list[str]]] = []

        sorted_groups = sorted(groups, key=lambda group: float(group["alias"]))
        for idx, group in enumerate(sorted_groups):
            next_alias = float(sorted_groups[idx + 1]["alias"]) if idx + 1 < len(sorted_groups) else width
            prev_group_keys = [k for k in ("alias", "particulars", "purchase", "pack") if k in sorted_groups[idx - 1]] if idx > 0 else []
            cur_group_keys = [k for k in ("alias", "particulars", "purchase", "pack") if k in group]
            prev_right = max(sorted_groups[idx - 1][k] for k in prev_group_keys) if idx > 0 else 0.0
            current_right = max(group[k] for k in cur_group_keys)

            left = 0.0 if idx == 0 else (prev_right + float(group["alias"])) / 2
            right = width if idx == len(sorted_groups) - 1 else (current_right + next_alias) / 2
            area = f"{max(left, 0):.1f},{height:.1f},{min(right, width):.1f},0"
            column_keys = [key for key in ("particulars", "purchase", "pack") if key in group]
            columns = ",".join(f"{float(group[key]):.1f}" for key in column_keys)

            tables = camelot.read_pdf(
                str(pdf_path),
                pages=str(page_num + 1),
                flavor="stream",
                suppress_stdout=True,
                edge_tol=500,
                table_areas=[area],
                columns=[columns],
            )
            for table in tables:
                if table.data:
                    result.append(table.data)

        return result if len(result) >= 2 else []
    finally:
        doc.close()


def _extract_page_tables_impl(pdf_path: Path, page_num: int, flavor: str) -> tuple[int, list[list[list[str]]]]:
    page_str = str(page_num + 1)  # Camelot uses 1-indexed pages
    page_result: list[list[list[str]]] = []

    if flavor in {"lattice", "stream"}:
        flavors = [flavor]
    else:
        flavors = ["lattice"]

    for current_flavor in flavors:
        try:
            tables = camelot.read_pdf(str(pdf_path), pages=page_str, flavor=current_flavor, suppress_stdout=True)
            if tables:
                for table in tables:
                    page_result.append(table.data)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Camelot failed on page %s (index=%s, flavor=%s): %s",
                page_num + 1,
                page_num,
                current_flavor,
                exc,
            )
            logger.debug("Camelot page failure details", exc_info=True)

    # Auto mode fallback for tables that have no ruling lines and collapse
    # into very narrow lattice outputs (commonly 1-3 columns).
    if flavor == "auto":
        repeated_header_result = _extract_repeated_header_regions(pdf_path, page_num)
        if repeated_header_result:
            page_result = repeated_header_result

        max_cols = max((max((len(row) for row in matrix), default=0) for matrix in page_result), default=0)
        needs_stream_fallback = not page_result or max_cols <= 3

        if needs_stream_fallback:
            stream_result: list[list[list[str]]] = []
            try:
                # edge_tol=500 merges sub-tables separated by section headers or
                # image rows so the full page is captured in one pass.
                tables = camelot.read_pdf(
                    str(pdf_path),
                    pages=page_str,
                    flavor="stream",
                    suppress_stdout=True,
                    edge_tol=500,
                )
                if tables:
                    for table in tables:
                        stream_result.append(table.data)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Camelot failed on page %s (index=%s, flavor=%s): %s",
                    page_num + 1,
                    page_num,
                    "stream",
                    exc,
                )
                logger.debug("Camelot page failure details", exc_info=True)

            if stream_result:
                page_result = stream_result

    return page_num, page_result


def _extract_page_tables_worker(
    pdf_path_str: str,
    page_num: int,
    flavor: str,
) -> tuple[int, list[list[list[str]]]]:
    """Pickle-safe process worker wrapper for page extraction."""
    return _extract_page_tables_impl(Path(pdf_path_str), page_num, flavor)


class CamelotExtractor(TableExtractor):
    """Extract tables using Camelot (free, works on native/searchable PDFs).

    Best for:
    - PDFs with clear table structure
    - Native (non-scanned) PDFs
    - Zero cost extraction

    Limitations:
    - Struggles with scanned/image PDFs
    - May miss complex multi-column layouts
    """

    def __init__(
        self,
        flavor: str = "auto",
        parallel_enabled: bool = True,
        extraction_mode: str = "thread",
        max_workers: int = 4,
        min_pages_for_parallel: int = 8,
    ):
        """
        Initialize Camelot extractor.

        Args:
            flavor: "auto" (default, tries both), "lattice" (line-based), or "stream" (space-based)
        """
        self.flavor = flavor
        self.parallel_enabled = parallel_enabled
        mode = extraction_mode.strip().lower()
        self.extraction_mode = mode if mode in {"thread", "process"} else "thread"
        self.max_workers = max(1, max_workers)
        self.min_pages_for_parallel = max(1, min_pages_for_parallel)

    def _extract_page_tables(self, pdf_path: Path, page_num: int) -> tuple[int, list[list[list[str]]]]:
        return _extract_page_tables_impl(pdf_path, page_num, self.flavor)

    def extract_tables(self, pdf_path: Path, candidate_pages: list[int]) -> dict[int, list[list[list[str]]]]:
        """Extract tables from candidate pages using Camelot."""
        result: dict[int, list[list[list[str]]]] = {}

        should_parallelize = (
            self.parallel_enabled
            and len(candidate_pages) >= self.min_pages_for_parallel
            and self.max_workers > 1
        )
        logger.info(
            "Camelot page extraction parallelization: %s (mode=%s pages=%s workers=%s threshold=%s)",
            "on" if should_parallelize else "off",
            self.extraction_mode if should_parallelize else "n/a",
            len(candidate_pages),
            self.max_workers,
            self.min_pages_for_parallel,
        )

        if should_parallelize:
            executor_cls = ThreadPoolExecutor if self.extraction_mode == "thread" else ProcessPoolExecutor
            with executor_cls(max_workers=self.max_workers) as executor:
                if self.extraction_mode == "thread":
                    futures = [
                        executor.submit(self._extract_page_tables, pdf_path, page_num)
                        for page_num in candidate_pages
                    ]
                else:
                    futures = [
                        executor.submit(_extract_page_tables_worker, str(pdf_path), page_num, self.flavor)
                        for page_num in candidate_pages
                    ]
                for future in as_completed(futures):
                    try:
                        page_num, page_result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Parallel page extraction task failed: %s", exc)
                        logger.debug("Parallel page extraction task failure details", exc_info=True)
                        continue
                    if page_result:
                        result[page_num] = page_result
        else:
            for page_num in candidate_pages:
                page_idx, page_result = self._extract_page_tables(pdf_path, page_num)
                if page_result:
                    result[page_idx] = page_result

        return result

    def supports_page_triage(self) -> bool:
        """Camelot requires processing entire pages, so page triage would not save much."""
        return False
