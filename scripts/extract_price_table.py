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


PRICE_PATTERN = re.compile(r"\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?")
ALIAS_PATTERN = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-_/\.]{2,}$")
PACK_PATTERN = re.compile(r"^[A-Za-z0-9\-_/\.xX]+$")


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
    
    logger.info(f"Page triage complete: {len(candidates)} candidate pages out of {len(page_scores)} total")
    logger.info(f"Candidate page numbers: {candidates}")
    
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
    match = PRICE_PATTERN.search(value.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
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


def normalize_rows(matrix: list[list[str]], page_number: int) -> list[NormalizedRow]:
    if len(matrix) < 2:
        return []

    headers = first_non_empty_row(matrix)
    mappings = build_column_mappings(headers)
    if not mappings:
        return []

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
    return normalized


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
        for matrix in tables_on_page:
            rows_before = len(all_rows)
            all_rows.extend(normalize_rows(matrix, page_number=page_num + 1))
            rows_after = len(all_rows)
            rows_extracted = rows_after - rows_before
            table_count += 1
            logger.debug(f"  Table {table_count}: extracted {rows_extracted} rows")

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
