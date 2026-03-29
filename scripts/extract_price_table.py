#!/usr/bin/env python3
"""Extract product price rows from large vendor PDFs into an Excel sheet.

Pipeline:
1) Page triage with fast native text scanning (PyMuPDF)
2) Pluggable table extraction backend (Camelot, Document AI, etc.)
3) Deterministic header mapping + strict row normalization
4) Excel export with exact extracted values only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz
from dotenv import load_dotenv
from openpyxl import Workbook
from rapidfuzz import fuzz

from extractors import CamelotExtractor, DocumentAIExtractor, TableExtractor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


KEYWORD_WEIGHTS: dict[str, int] = {
    "reference": 2,
    "ref no": 2,
    "reference no": 3,
    "item code": 3,
    "part no": 2,
    "code": 1,
    "alias": 2,
    "mrp": 3,
    "unit mrp": 3,
    "unit price": 3,
    "price": 1,
    "purchase": 2,
    "pack": 2,
    "std pkg": 2,
    "pkg": 1,
    "particular": 2,
    "description": 2,
}


REQUIRED_PROFILE_ROLES = {"alias", "purchase", "particulars", "pack"}

ACTIVE_ROLE_SYNONYMS: dict[str, list[str]] = {
    "alias": [],
    "purchase": [],
    "particulars": [],
    "pack": [],
}


def load_profile(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Profile must be a JSON object: {path}")

    profile: dict[str, list[str]] = {}
    for role in REQUIRED_PROFILE_ROLES:
        synonyms = payload.get(role)
        if not isinstance(synonyms, list):
            raise ValueError(f"Role '{role}' must be a list in profile: {path}")
        cleaned = [str(s).strip() for s in synonyms if str(s).strip()]
        if not cleaned:
            raise ValueError(f"Role '{role}' list is empty in profile: {path}")
        profile[role] = cleaned

    return profile


def configure_role_synonyms(
    input_pdf: Path,
    header_profile_file: Path | None,
    header_profile_dir: Path,
) -> None:
    """Load manual role synonyms from per-PDF profile file.

    Priority:
    1) --header-profile-file (explicit)
    2) <header_profile_dir>/<pdf_stem>.json (auto)
    3) <header_profile_dir>/default.json (fallback)
    """
    global ACTIVE_ROLE_SYNONYMS

    profile_path: Path | None = None
    if header_profile_file is not None:
        profile_path = header_profile_file
    else:
        auto_path = header_profile_dir / f"{input_pdf.stem}.json"
        if auto_path.exists():
            profile_path = auto_path
        else:
            profile_path = header_profile_dir / "default.json"

    if not profile_path.exists():
        logger.error(
            "Header profile not found. Provide --header-profile-file or create "
            f"{header_profile_dir / (input_pdf.stem + '.json')} or {header_profile_dir / 'default.json'}."
        )
        raise SystemExit("No header profile available.")

    try:
        ACTIVE_ROLE_SYNONYMS = load_profile(profile_path)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to load header profile '{profile_path}': {exc}")
        raise SystemExit("Invalid header profile.")

    logger.info(f"Loaded header profile: {profile_path}")
    logger.info(f"Profile roles applied: {', '.join(sorted(ACTIVE_ROLE_SYNONYMS.keys()))}")


PRICE_PATTERN = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
ALIAS_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}$")
PACK_PATTERN = re.compile(r"^[A-Za-z0-9\-_/\.xX]+$")
PACK_LINE_HINT = re.compile(r"^(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?\s*(?:nos?|pcs?|pc|set|box|pkt|unit|uom))$", re.IGNORECASE)
NON_ALIAS_UNIT_PATTERN = re.compile(r"^\d+(?:\.\d+)?(?:MA|A|P|V|KV|W|KW|MW|HZ|KA)$", re.IGNORECASE)


@dataclass
class NormalizedRow:
    particulars: str
    alias: str
    purchase: float
    pack: str
    source_page: int


def normalize_header(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def header_role_score(header: str, role: str) -> int:
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


def page_score(text: str) -> int:
    t = text.lower()
    score = 0
    for kw, weight in KEYWORD_WEIGHTS.items():
        if kw in t:
            score += weight
    return score


def select_candidate_pages(pdf_path: Path, min_score: int = 2) -> tuple[list[int], dict[int, int]]:
    doc = fitz.open(pdf_path)
    page_scores: dict[int, int] = {}
    candidates: list[int] = []

    logger.info(f"Starting page triage on {pdf_path.name}")
    logger.debug(f"Minimum page score threshold: {min_score}")

    for i, page in enumerate(doc):
        text = page.get_text("text")
        score = page_score(text)
        page_scores[i] = score
        if score >= min_score:
            candidates.append(i)
            logger.debug(f"Page {i + 1}: score={score} (CANDIDATE)")
        else:
            logger.debug(f"Page {i + 1}: score={score} (skipped)")

    if not candidates:
        logger.warning("No pages matched keyword threshold. Selecting top 30 pages by score.")
        candidates = sorted(page_scores.keys(), key=lambda k: page_scores[k], reverse=True)[: min(30, len(page_scores))]
        logger.info(f"Top {len(candidates)} pages selected by score")

    doc.close()
    candidates = sorted(set(candidates))

    candidate_pages_human = [p + 1 for p in candidates]

    logger.info(f"Page triage complete: {len(candidates)} candidate pages out of {len(page_scores)} total")
    logger.info(f"Candidate page numbers (0-index): {candidates}")
    logger.info(f"Candidate page numbers (human): {candidate_pages_human}")

    return candidates, page_scores


def build_single_page_pdf_bytes(pdf_path: Path, page_index: int) -> bytes:
    src = fitz.open(pdf_path)
    single = fitz.open()
    single.insert_pdf(src, from_page=page_index, to_page=page_index)
    data = single.tobytes(garbage=3, deflate=True)
    single.close()
    src.close()
    return data


def first_non_empty_row(matrix: list[list[str]]) -> list[str]:
    for row in matrix:
        if any(cell.strip() for cell in row):
            return row
    return []


def nearest_index(target: int, choices: Iterable[int]) -> int | None:
    choices_list = list(choices)
    if not choices_list:
        return None
    return min(choices_list, key=lambda x: abs(x - target))


def build_column_mappings(headers: list[str]) -> list[dict[str, int]]:
    if not headers:
        return []

    scores_by_role: dict[str, list[int]] = {
        role: [header_role_score(h, role) for h in headers] for role in ACTIVE_ROLE_SYNONYMS
    }

    alias_cols = [i for i, s in enumerate(scores_by_role["alias"]) if s >= 70]
    purchase_cols = [i for i, s in enumerate(scores_by_role["purchase"]) if s >= 70]
    particulars_cols = [i for i, s in enumerate(scores_by_role["particulars"]) if s >= 70]
    pack_cols = [i for i, s in enumerate(scores_by_role["pack"]) if s >= 70]

    mappings: list[dict[str, int]] = []

    # Handle repeated header blocks, e.g. Alias/Price repeated twice on same row.
    if len(alias_cols) >= 2 and len(purchase_cols) >= 2:
        used_purchase: set[int] = set()
        for alias_idx in sorted(alias_cols):
            candidate_purchase = [p for p in purchase_cols if p not in used_purchase]
            p_idx = nearest_index(alias_idx, candidate_purchase)
            if p_idx is None:
                continue
            used_purchase.add(p_idx)

            mapping = {"alias": alias_idx, "purchase": p_idx}
            part_idx = nearest_index(alias_idx, particulars_cols)
            pack_idx = nearest_index(alias_idx, pack_cols)
            if part_idx is not None and abs(part_idx - alias_idx) <= 4:
                mapping["particulars"] = part_idx
            if pack_idx is not None and abs(pack_idx - alias_idx) <= 4:
                mapping["pack"] = pack_idx
            mappings.append(mapping)

    if mappings:
        return mappings

    alias_best = max(range(len(headers)), key=lambda i: scores_by_role["alias"][i])
    purchase_best = max(range(len(headers)), key=lambda i: scores_by_role["purchase"][i])
    if scores_by_role["alias"][alias_best] < 60 or scores_by_role["purchase"][purchase_best] < 60:
        return []

    mapping = {"alias": alias_best, "purchase": purchase_best}
    if particulars_cols:
        mapping["particulars"] = max(particulars_cols, key=lambda i: scores_by_role["particulars"][i])
    if pack_cols:
        mapping["pack"] = max(pack_cols, key=lambda i: scores_by_role["pack"][i])
    return [mapping]


def parse_price(value: str) -> float | None:
    if not value:
        return None
    compact = value.replace(" ", "")
    if re.search(r"[A-Za-z]", compact):
        return None
    normalized = compact.replace(",,", ",")
    if normalized.endswith(".-"):
        normalized = normalized[:-2]
    normalized = normalized.strip(".")
    if not normalized or not PRICE_PATTERN.fullmatch(normalized):
        return None
    try:
        return float(normalized.replace(",", ""))
    except ValueError:
        return None


def clean_alias(value: str) -> str:
    value = value.strip().upper()
    value = re.sub(r"\s+", "", value)
    return value


def clean_pack(value: str) -> str:
    v = " ".join(value.split()).strip()
    if not v:
        return ""
    if PACK_PATTERN.match(v):
        return v
    return ""


def looks_like_alias(value: str) -> bool:
    if not value:
        return False
    if NON_ALIAS_UNIT_PATTERN.match(value):
        return False
    return bool(ALIAS_PATTERN.match(value))


def fallback_particulars(row: list[str], used_indices: set[int]) -> str:
    candidates = [
        cell.strip()
        for idx, cell in enumerate(row)
        if idx not in used_indices and cell and not PRICE_PATTERN.fullmatch(cell.replace(",", ""))
    ]
    if not candidates:
        return ""
    return max(candidates, key=len)


def split_cell_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def split_pack_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for line in split_cell_lines(value):
        # Camelot sometimes groups repeated pack values as "1 1 1 1".
        if re.fullmatch(r"\d+(?:\s+\d+)+", line):
            tokens.extend(part.strip() for part in line.split() if part.strip())
        else:
            tokens.append(line)
    return tokens


def looks_like_alias_line(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    # Product codes are usually single tokens; values like "4 module"
    # are description lines and should not be turned into aliases.
    if re.search(r"\s", raw):
        return False
    return looks_like_alias(clean_alias(raw))


def is_probable_section_heading(value: str) -> bool:
    """Detect generic section labels (e.g. product family titles) near data rows."""
    text = " ".join(value.split()).strip()
    if not text:
        return False
    if looks_like_alias_line(text):
        return False
    if parse_price(text) is not None:
        return False

    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) < 3:
        return False

    # Pure alphabetic multi-word lines are usually section headers, not particulars.
    if all(re.fullmatch(r"[A-Za-z&()'.-]+", w) for w in words):
        return True
    return False


def extract_alias_entries(value: str) -> list[tuple[str, str]]:
    """Extract alias lines and nearby contextual particulars from a cell."""
    lines = split_cell_lines(value)
    out: list[tuple[str, str]] = []

    for idx, line in enumerate(lines):
        if not looks_like_alias_line(line):
            continue

        alias = clean_alias(line)
        parts: list[str] = []
        for neighbor_idx in (idx - 1, idx + 1):
            if neighbor_idx < 0 or neighbor_idx >= len(lines):
                continue
            candidate = lines[neighbor_idx].strip()
            if not candidate:
                continue
            if looks_like_alias_line(candidate):
                continue
            if is_probable_section_heading(candidate):
                continue
            if parse_price(candidate) is not None and not re.search(r"[A-Za-z+/]", candidate):
                continue
            if candidate not in parts:
                parts.append(candidate)

        out.append((alias, " / ".join(parts)))

    return out


def line_alias_count(value: str) -> int:
    lines = split_cell_lines(value)
    return sum(1 for line in lines if looks_like_alias_line(line))


def line_price_count(value: str) -> int:
    lines = split_cell_lines(value)
    return sum(1 for line in lines if parse_price(line) is not None)


def line_pack_count(value: str) -> int:
    lines = split_pack_tokens(value)
    count = 0
    for line in lines:
        line = line.strip()
        if PACK_LINE_HINT.match(line):
            count += 1
            continue

        numeric_value = parse_price(line)
        if numeric_value is not None and numeric_value.is_integer() and 0 < numeric_value <= 100:
            count += 1
    return count


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
    # Collapse only when matrix looks vertically fragmented.
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


def pack_column_quality(value: str) -> int:
    """Score how likely a multiline cell represents package quantities.

    Slash-form values like 1/12 are strongly preferred over plain integers,
    which may represent current ratings (In A) in some catalogs.
    """
    lines = split_pack_tokens(value)
    if not lines:
        return 0

    score = 0
    pack_like = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue

        if looks_like_alias_line(line):
            score -= 4
            continue

        if re.search(r"[A-Za-z]", line) and "/" not in line:
            score -= 2

        if "/" in line:
            score += 5
            pack_like += 1
        if PACK_LINE_HINT.match(line):
            score += 3
            pack_like += 1

        numeric_value = parse_price(line)
        if numeric_value is not None and numeric_value.is_integer() and 0 < numeric_value <= 100:
            if "/" in line:
                pack_like += 1
            else:
                score += 2
                pack_like += 1

    if lines and (pack_like / len(lines)) >= 0.7:
        score += 4

    return score


def select_pack_column(alias_idx: int, pack_cols: list[int], row: list[str]) -> int | None:
    if not pack_cols:
        return None

    def rank(idx: int) -> tuple[int, int, int, int]:
        return (
            pack_column_quality(row[idx]),
            -abs(idx - alias_idx),
            1 if idx > alias_idx else 0,
            idx,
        )

    return max(pack_cols, key=rank)


def pack_value_quality(pack: str) -> int:
    if not pack:
        return 0
    if looks_like_alias_line(pack):
        return -3
    if "/" in pack:
        return 3
    if re.fullmatch(r"\d+", pack):
        return 2
    if PACK_PATTERN.fullmatch(pack):
        return 1
    return 0


def normalized_row_quality(row: NormalizedRow) -> int:
    return pack_value_quality(row.pack) + (1 if row.particulars else 0)


def extract_packed_multiline_rows(matrix: list[list[str]], page_number: int) -> list[NormalizedRow]:
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
        pack_cols = [idx for idx, cell in enumerate(row) if line_pack_count(cell) >= 2]

        if not alias_cols or not purchase_cols:
            continue

        pairs: list[tuple[int, int]] = []
        used_purchase: set[int] = set()
        for alias_idx in sorted(alias_cols):
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
            pack_idx = select_pack_column(alias_idx, pack_cols, row)
            if pack_idx is not None:
                pack_lines = split_pack_tokens(row[pack_idx])

            # Split non-alias/price/pack text columns into lines so we can
            # compose per-sub-row particulars from matching line indexes.
            used_cols = {alias_idx, purchase_idx}
            if pack_idx is not None:
                used_cols.add(pack_idx)
            text_col_lines: list[list[str]] = [
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
                alias_particulars = alias_entries[i][1] if i < len(alias_entries) else ""
                purchase = parse_price(purchase_lines[i]) if i < len(purchase_lines) else None
                if not looks_like_alias(alias) or purchase is None:
                    continue

                pack = ""
                if i < len(pack_lines):
                    pack = clean_pack(pack_lines[i])

                # For each text column pick the line at position i if available,
                # otherwise the single value if the column is not multiline.
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
                    if val and not PRICE_PATTERN.fullmatch(val.replace(",", "")):
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

    # Keep best candidate per alias+purchase from this fallback pass.
    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in normalized:
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row

    return list(best_by_key.values())


def normalize_rows(matrix: list[list[str]], page_number: int) -> list[NormalizedRow]:
    if len(matrix) < 2:
        return []

    headers = first_non_empty_row(matrix)
    mappings = build_column_mappings(headers)
    if not mappings:
        return extract_packed_multiline_rows(matrix, page_number=page_number)

    data_rows = matrix[matrix.index(headers) + 1 :]
    normalized: list[NormalizedRow] = []

    for row in data_rows:
        if not any(cell.strip() for cell in row):
            continue
        for mapping in mappings:
            alias_raw = row[mapping["alias"]].strip() if mapping["alias"] < len(row) else ""
            price_raw = row[mapping["purchase"]].strip() if mapping["purchase"] < len(row) else ""

            alias = clean_alias(alias_raw)
            purchase = parse_price(price_raw)
            if not looks_like_alias(alias) or purchase is None:
                continue

            pack = ""
            if "pack" in mapping and mapping["pack"] < len(row):
                pack = clean_pack(row[mapping["pack"]])

            particulars = ""
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
        return normalized

    return extract_packed_multiline_rows(matrix, page_number=page_number)


def deduplicate_rows(rows: list[NormalizedRow]) -> list[NormalizedRow]:
    seen: set[tuple[str, float]] = set()
    out: list[NormalizedRow] = []
    for row in rows:
        key = (row.alias, row.purchase)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def export_xlsx(rows: list[NormalizedRow], output_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    if ws is None:
        raise RuntimeError("Failed to access workbook active sheet")
    ws.title = "extracted_prices"
    ws.append(["particulars", "alias", "purchase", "pack", "source_page"])
    for row in rows:
        ws.append([row.particulars, row.alias, row.purchase, row.pack, row.source_page])
    wb.save(output_path)


def get_extractor(backend: str, env: dict[str, str], verbose: bool = False) -> TableExtractor:
    """Factory to create the appropriate table extractor backend."""
    backend = backend.lower()

    if backend == "camelot":
        logger.info("Initializing Camelot extractor (free, for native PDFs)")
        return CamelotExtractor(flavor="lattice")

    elif backend == "docai":
        logger.info("Initializing Document AI extractor (paid, for scanned/complex PDFs)")
        project_id = env.get("GOOGLE_CLOUD_PROJECT", "")
        location = env.get("GOOGLE_CLOUD_LOCATION", "us")
        processor_id = env.get("GOOGLE_DOCAI_PROCESSOR_ID", "")
        processor_version = env.get("GOOGLE_DOCAI_PROCESSOR_VERSION", "")

        if not project_id or not processor_id:
            logger.error("Missing Google Cloud credentials for Document AI backend.")
            raise SystemExit("Missing Google Cloud credentials for Document AI backend.")
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            logger.error("Missing GOOGLE_APPLICATION_CREDENTIALS environment variable.")
            raise SystemExit("Missing GOOGLE_APPLICATION_CREDENTIALS environment variable.")

        logger.debug(f"Document AI: project={project_id}, location={location}, processor={processor_id}")
        return DocumentAIExtractor(project_id, location, processor_id, processor_version)

    else:
        logger.error(f"Unknown extraction backend: {backend}")
        raise SystemExit(f"Unknown extraction backend: {backend}. Use 'camelot' or 'docai'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract alias, purchase, particulars, and pack from PDF tables.")
    parser.add_argument("--env-file", default=".env", type=Path, help="Path to .env file (default: .env)")
    parser.add_argument("--input-pdf", required=True, type=Path, help="Path to source PDF")
    parser.add_argument(
        "--output-xlsx", default=Path("output/extracted_prices.xlsx"), type=Path, help="Output XLSX file"
    )
    parser.add_argument(
        "--backend",
        default="",
        help="Extraction backend: 'camelot' (free) or 'docai' (paid). Defaults to EXTRACTION_BACKEND env var or 'camelot'.",
    )
    parser.add_argument(
        "--header-profile-file",
        type=Path,
        default=None,
        help="Optional explicit header profile JSON file for this PDF.",
    )
    parser.add_argument(
        "--header-profile-dir",
        type=Path,
        default=Path("header_profiles"),
        help="Directory for auto-loaded per-PDF header profiles (default: header_profiles).",
    )
    parser.add_argument("--min-page-score", default=2, type=int, help="Minimum triage score to process a page")
    parser.add_argument("--max-pages", default=0, type=int, help="Limit candidate page count for testing (0 = no limit)")
    parser.add_argument("--verbose", action="store_true", help="Print progress details")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Set verbose mode
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.debug("Verbose logging enabled")

    logger.info("=" * 80)
    logger.info("Starting PDF price extraction pipeline")
    logger.info(f"Input PDF: {args.input_pdf}")
    logger.info(f"Output XLSX: {args.output_xlsx}")

    if args.env_file.exists():
        logger.info(f"Loading environment from: {args.env_file}")
        load_dotenv(dotenv_path=args.env_file)
    else:
        logger.debug("No .env file found, using current environment")
        load_dotenv()

    if not args.input_pdf.exists():
        logger.error(f"Input PDF not found: {args.input_pdf}")
        raise SystemExit(f"Input PDF not found: {args.input_pdf}")

    configure_role_synonyms(
        input_pdf=args.input_pdf,
        header_profile_file=args.header_profile_file,
        header_profile_dir=args.header_profile_dir,
    )

    backend = args.backend or os.getenv("EXTRACTION_BACKEND", "camelot")
    logger.info(f"Selected extraction backend: {backend}")
    
    env = dict(os.environ)
    extractor = get_extractor(backend, env, verbose=args.verbose)

    logger.info("Starting page triage...")
    candidate_pages, scores = select_candidate_pages(args.input_pdf, min_score=args.min_page_score)
    
    if args.max_pages > 0:
        original_count = len(candidate_pages)
        candidate_pages = candidate_pages[: args.max_pages]
        logger.info(f"Limited to first {args.max_pages} pages (originally {original_count})")

    logger.info(f"Total pages to process: {len(candidate_pages)}")
    logger.info("Extracting tables from candidate pages...")

    all_rows: list[NormalizedRow] = []
    page_tables = extractor.extract_tables(args.input_pdf, candidate_pages)

    logger.info(f"Tables extracted from {len(page_tables)} pages")

    for page_num, tables_on_page in page_tables.items():
        logger.info(f"Processing page {page_num + 1}: found {len(tables_on_page)} table(s)")
        table_count = 0
        page_rows_extracted = 0
        for matrix in tables_on_page:
            rows_before = len(all_rows)
            all_rows.extend(normalize_rows(matrix, page_number=page_num + 1))
            rows_after = len(all_rows)
            rows_extracted = rows_after - rows_before
            table_count += 1
            page_rows_extracted += rows_extracted
            logger.info(f"  Page {page_num + 1}, table {table_count}: extracted {rows_extracted} row(s)")
        logger.info(f"Page {page_num + 1}: extracted {page_rows_extracted} row(s) from {table_count} table(s)")

    logger.info(f"Total rows extracted (pre-dedup): {len(all_rows)}")

    if all_rows:
        logger.info("Deduplicating rows...")
        final_rows = deduplicate_rows(all_rows)
        duplicate_count = len(all_rows) - len(final_rows)
        logger.info(f"Removed {duplicate_count} duplicate rows")
    else:
        logger.warning("No rows were extracted!")
        final_rows = []

    logger.info(f"Final row count: {len(final_rows)}")

    args.output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing output to: {args.output_xlsx}")
    export_xlsx(final_rows, args.output_xlsx)

    logger.info("=" * 80)
    logger.info("EXTRACTION COMPLETE")
    logger.info(f"  Candidate pages: {len(candidate_pages)}")
    logger.info(f"  Extracted rows (pre-dedup): {len(all_rows)}")
    logger.info(f"  Final rows: {len(final_rows)}")
    logger.info(f"  Output file: {args.output_xlsx}")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
