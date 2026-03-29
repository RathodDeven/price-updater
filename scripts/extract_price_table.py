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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import logging
import os
from pathlib import Path
import time

from dotenv import load_dotenv

from core.config import configure_role_synonyms, load_parallel_processing_config
from core.deduplication import deduplicate_rows
from core.export import export_xlsx
from core.normalization import normalize_rows
from core.page_triage import load_keyword_weights, select_candidate_pages
from extractors import CamelotExtractor, DocumentAIExtractor, TableExtractor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _process_page_tables(
    page_num: int,
    tables_on_page: list[list[list[str]]],
    include_particulars: bool,
    include_pack: bool,
) -> tuple[int, list, list[int]]:
    page_rows: list = []
    per_table_counts: list[int] = []
    for matrix in tables_on_page:
        extracted = normalize_rows(
            matrix,
            page_number=page_num + 1,
            include_particulars=include_particulars,
            include_pack=include_pack,
        )
        page_rows.extend(extracted)
        per_table_counts.append(len(extracted))
    return page_num, page_rows, per_table_counts


def _format_seconds(seconds: float) -> str:
    """Format elapsed seconds for human-readable logs."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m {rem:.2f}s"
    hours, rem_minutes = divmod(int(minutes), 60)
    return f"{hours}h {rem_minutes}m {rem:.2f}s"


def get_extractor(backend: str, env: dict[str, str], verbose: bool = False) -> TableExtractor:
    """Factory to create the appropriate table extractor backend."""
    backend = backend.lower()

    if backend == "camelot":
        parallel = load_parallel_processing_config(env)
        logger.info("Initializing Camelot extractor (free, for native PDFs)")
        return CamelotExtractor(
            flavor="auto",
            parallel_enabled=parallel.enabled,
            extraction_mode=parallel.extraction_mode,
            max_workers=parallel.extraction_workers,
            min_pages_for_parallel=parallel.min_pages_for_parallel,
        )

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
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Extract alias/purchase rows from PDF tables (particulars/pack optional)."
    )
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
    parser.add_argument(
        "--target-page",
        default=0,
        type=int,
        help="Process only a specific page (1-indexed, 0 = all). Useful for testing single tables.",
    )
    parser.add_argument(
        "--triage-keywords-file",
        type=Path,
        default=None,
        help="Optional JSON file to extend/override page triage keyword weights.",
    )
    parser.add_argument(
        "--include-particulars",
        action="store_true",
        help="Also extract and export particulars column. Off by default for faster runs.",
    )
    parser.add_argument(
        "--include-pack",
        action="store_true",
        help="Also extract and export pack column. Off by default for faster runs.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print progress details")
    return parser.parse_args()


def main() -> None:
    """Main extraction pipeline."""
    args = parse_args()
    run_start = time.perf_counter()

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
    parallel_cfg = load_parallel_processing_config(env)
    logger.info(
        "Parallel config: enabled=%s extraction_mode=%s extraction_workers=%s normalization_mode=%s normalization_workers=%s min_pages=%s",
        parallel_cfg.enabled,
        parallel_cfg.extraction_mode,
        parallel_cfg.extraction_workers,
        parallel_cfg.normalization_mode,
        parallel_cfg.normalization_workers,
        parallel_cfg.min_pages_for_parallel,
    )

    logger.info("Starting page triage...")
    triage_start = time.perf_counter()
    keyword_weights = load_keyword_weights(args.triage_keywords_file)
    candidate_pages, scores = select_candidate_pages(
        args.input_pdf,
        min_score=args.min_page_score,
        keyword_weights=keyword_weights,
    )
    logger.info("Page triage duration: %s", _format_seconds(time.perf_counter() - triage_start))

    if args.max_pages > 0:
        original_count = len(candidate_pages)
        candidate_pages = candidate_pages[: args.max_pages]
        logger.info(f"Limited to first {args.max_pages} pages (originally {original_count})")

    if args.target_page > 0:
        # target_page is 1-indexed, candidate_pages are 0-indexed
        target_idx = args.target_page - 1
        if target_idx in candidate_pages:
            logger.info(f"Targeting specific page: {args.target_page} (0-index: {target_idx})")
            candidate_pages = [target_idx]
        else:
            logger.warning(f"Target page {args.target_page} was not selected by triage")
            candidate_pages = [target_idx] if 0 <= target_idx < 1000 else []

    logger.info(f"Total pages to process: {len(candidate_pages)}")
    logger.info("Extracting tables from candidate pages...")
    logger.info(
        "Enabled optional fields: particulars=%s, pack=%s",
        "on" if args.include_particulars else "off",
        "on" if args.include_pack else "off",
    )

    all_rows = []
    extraction_start = time.perf_counter()
    page_tables = extractor.extract_tables(args.input_pdf, candidate_pages)
    logger.info("Table extraction duration: %s", _format_seconds(time.perf_counter() - extraction_start))

    logger.info(f"Tables extracted from {len(page_tables)} pages")
    normalization_start = time.perf_counter()

    page_items = sorted(page_tables.items(), key=lambda x: x[0])
    should_parallelize_normalization = (
        parallel_cfg.enabled
        and parallel_cfg.normalization_mode != "off"
        and parallel_cfg.normalization_workers > 1
        and len(page_items) >= parallel_cfg.min_pages_for_parallel
    )
    logger.info(
        "Normalization parallelization: %s (mode=%s pages=%s threshold=%s)",
        "on" if should_parallelize_normalization else "off",
        parallel_cfg.normalization_mode if should_parallelize_normalization else "off",
        len(page_items),
        parallel_cfg.min_pages_for_parallel,
    )

    page_results: list[tuple[int, list, list[int]]] = []
    if should_parallelize_normalization:
        executor_cls = ThreadPoolExecutor if parallel_cfg.normalization_mode == "thread" else ProcessPoolExecutor
        with executor_cls(max_workers=parallel_cfg.normalization_workers) as executor:
            futures = [
                executor.submit(
                    _process_page_tables,
                    page_num,
                    tables,
                    args.include_particulars,
                    args.include_pack,
                )
                for page_num, tables in page_items
            ]
            for future in as_completed(futures):
                page_results.append(future.result())
        page_results.sort(key=lambda x: x[0])
    else:
        for page_num, tables_on_page in page_items:
            page_results.append(
                _process_page_tables(
                    page_num,
                    tables_on_page,
                    args.include_particulars,
                    args.include_pack,
                )
            )

    for page_num, page_rows, per_table_counts in page_results:
        logger.info(f"Processing page {page_num + 1}: found {len(per_table_counts)} table(s)")
        for table_idx, rows_extracted in enumerate(per_table_counts, start=1):
            logger.info(f"  Page {page_num + 1}, table {table_idx}: extracted {rows_extracted} row(s)")
        logger.info(
            f"Page {page_num + 1}: extracted {sum(per_table_counts)} row(s) from {len(per_table_counts)} table(s)"
        )
        all_rows.extend(page_rows)
    logger.info("Row normalization duration: %s", _format_seconds(time.perf_counter() - normalization_start))

    logger.info(f"Total rows extracted (pre-dedup): {len(all_rows)}")

    if all_rows:
        logger.info("Deduplicating rows...")
        dedup_start = time.perf_counter()
        final_rows = deduplicate_rows(all_rows)
        duplicate_count = len(all_rows) - len(final_rows)
        logger.info(f"Removed {duplicate_count} duplicate rows")
        logger.info("Deduplication duration: %s", _format_seconds(time.perf_counter() - dedup_start))
    else:
        logger.warning("No rows were extracted!")
        final_rows = []

    logger.info(f"Final row count: {len(final_rows)}")

    args.output_xlsx.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Writing output to: {args.output_xlsx}")
    export_start = time.perf_counter()
    export_xlsx(
        final_rows,
        args.output_xlsx,
        include_particulars=args.include_particulars,
        include_pack=args.include_pack,
    )
    logger.info("Export duration: %s", _format_seconds(time.perf_counter() - export_start))

    logger.info("=" * 80)
    logger.info("EXTRACTION COMPLETE")
    logger.info(f"  Candidate pages: {len(candidate_pages)}")
    logger.info(f"  Extracted rows (pre-dedup): {len(all_rows)}")
    logger.info(f"  Final rows: {len(final_rows)}")
    logger.info(f"  Output file: {args.output_xlsx}")
    logger.info("  Total runtime: %s", _format_seconds(time.perf_counter() - run_start))
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
