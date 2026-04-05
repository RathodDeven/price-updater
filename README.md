# Price Updater - PDF Table Extractor

This repository contains a Python script that extracts price rows from vendor PDFs into Excel.

Primary script:
- scripts/extract_price_table.py

Output columns:
- alias (product id / reference no / item code)
- purchase (MRP / unit price)
- source_page

Default output is intentionally minimal for performance on large catalogs:
- `alias`
- `purchase`
- `source_page`

Optional columns can be enabled with CLI flags:
- `--include-particulars`
- `--include-pack`

## Is any LLM used?

No. The current script does not call OpenAI or any other LLM.

Current flow is fully deterministic:
1. Page triage with keyword scoring
2. Table extraction backend (Camelot or Document AI)
3. Header-to-role mapping with synonym + fuzzy matching
4. Row validation and export

The pipeline also supports page-level parallel processing for faster runs on large PDFs.

## Edge-Case Summary (Quick)

- Repeated alias/price header blocks on one row
- Header naming variation across vendors
- Multiple sub-tables on one page
- Decorative/spacing rows between products
- Continuation particulars rows
- Headerless packed multiline matrices
- Fragmented sparse matrix rows
- Pack vs price ambiguity in small integers
- False alias rejection (unit-like tokens)
- Cross-parser duplicate candidate rows
- Shared purchase column across multiple reference columns
- Compact horizontal tables collapsed into one dense text column

## How per-PDF header mapping works

The script now supports manual per-PDF header profiles.

Priority order used at runtime:
1. `--header-profile-file /path/to/profile.json` (explicit profile)
2. `header_profiles/<input_pdf_stem>.json` (auto-detected profile)
3. `header_profiles/default.json` (fallback)

Meaning:
- If input PDF is `samples/sinova_catalog.pdf`, script will auto-load `header_profiles/sinova_catalog.json` if it exists.
- If input PDF is `samples/sample_1.pdf`, script will auto-load `header_profiles/sample_1.json` if it exists.
- If no per-PDF file exists, script loads `header_profiles/default.json` from the configured profile directory.
- If neither exists, script exits with an error.

Role mapping logic (profile keys):
- `alias`: id/reference code equivalents
- `purchase`: MRP/unit price equivalents
- `particulars`: description equivalents
- `pack`: pack equivalents

Page triage now reuses the same role synonym lists from the active profile for marker checks.
This keeps triage and extraction aligned and avoids maintaining a separate
`triage.role_markers` list in profile JSON.

Default role weights for triage are centralized in `scripts/core/config.py`
(`DEFAULT_TRIAGE_ROLE_WEIGHTS`). Optional profile override is still supported
via `triage.role_weights`.

Weight precedence at runtime:
1. Start from `DEFAULT_TRIAGE_ROLE_WEIGHTS` in `scripts/core/config.py`
2. If the active profile JSON has `triage.role_weights`, those role values override defaults for that run

Example optional override in a profile:

```json
{
  "triage": {
    "role_weights": {
      "alias": 2,
      "purchase": 3,
      "particulars": 2,
      "pack": 2
    }
  }
}
```

Then each extracted row is normalized with mandatory fields:
- `alias`
- `purchase`
- `source_page`

Optional fields (disabled by default):
- `particulars` (`--include-particulars`)
- `pack` (`--include-pack`)

Example profile file:

```json
{
  "alias": ["reference no", "ref no", "item code", "cat.nos"],
  "purchase": ["unit mrp", "mrp", "mrp* /unit", "price"],
  "particulars": ["description", "particulars", "product description"],
  "pack": ["std. pkg. (nos.)", "pack", "nos"]
}
```

An example is included at `header_profiles/example_vendor.json`.

## Mandatory fields and row skip rule

This is the most important rule and is already enforced in code:
- alias is mandatory
- purchase is mandatory

If either alias or purchase is missing/invalid in a row, that row is skipped.

Validation details:
- alias must match a code-like pattern (alphanumeric style id)
- purchase must parse as a numeric value

So if a row has description but no valid alias or no valid purchase, it will not be exported.

## Edge Cases Currently Handled

The extractor currently handles these generic table patterns:

1. Repeated alias and price blocks in one table row
2. Header naming variation across vendors and catalogs
3. Multiple logical sub-tables on one physical page
4. Empty, decorative, or spacing rows
5. Continuation rows for particulars/description text
6. Headerless packed multiline tables
7. Fragmented sparse matrices
8. Pack vs purchase ambiguity
9. False alias prevention for non-code tokens
10. Split multi-row headers with bullets or labels above the table
11. Pole labels leaking into alias candidates
12. Duplicate candidate rows from competing parsers
13. Shared purchase columns across multiple reference columns
14. Compact horizontal tables collapsed into one dense text column
15. Reference codes with raised footnote markers
16. Two-column catalog spreads where repeated Cat.Nos and MRP/Unit headers appear lower on the page and item grids begin after intervening text/feature blocks
17. Vertical dense-column tables where alias and purchase are stacked in one merged text column and pack appears in a nearby column
18. Compact vertical blocks where Cat.Nos is separate but MRP and Pack are stacked line-wise in one column
19. Flattened accessory matrices where repeated Cat.Nos MRP pairs appear as token streams in one row/cell
20. Compact vertical fallback now requires explicit purchase-header evidence (for example MRP/Unit markers); if purchase column is absent/blank, rows are skipped instead of borrowing numeric values from rated-current columns
21. Dense merged cells now reject description-only numerics (for example DRX 100) as purchase unless purchase-role evidence exists; rows with blank MRP are skipped
22. Headerless follow-on table fragments on the same page can inherit alias/purchase column roles from the nearest prior header-mapped table, so extraction stays column-role driven instead of falling back to broad alias token guessing
23. Stacked Cat.No/MRP/Pack cells within a single column are parsed as triplets — MRP is the first qualified price (≥50), not pack
24. Price-on-request markers (■/\uf06e) skip the row instead of extracting current ratings or pack values as MRP
25. Reversed MRP-before-alias stacking (e.g. `1800\n4242 11`) is recognised by the stream parser
26. Cross-line alias groups prevented — only horizontal whitespace (space/tab) separates alias digit groups, not newlines
27. Adjacent-cell stream fallback now ignores price candidates from alphabetic description cells (for example `... Cover Joint 75`) so numeric suffixes in text do not override real MRP columns
28. Split mapped rows with the Cat.No at the start of the particulars/description cell now recover leading alphanumeric aliases (for example `AC21104MW ...`) instead of dropping those rows
29. Previous-row purchase salvage now prefers the mapped MRP column and ignores descriptive alias-column text blocks, preventing alias/purchase inversion on continuation rows
30. When a mapped MRP cell is blank but a separate pack column is populated, trailing numerics in description text are no longer promoted to purchase by default
31. Header detection scans deeper into long table preambles, so real Cat.Nos/MRP header rows below feature bullets are mapped instead of falling back to headerless parsing
32. Extra alias emission from mapped multiline cells now requires a strong catalog-code shape, so contact-configuration labels such as `2 NO`/`4 NC` are not exported as aliases
33. Merged spread headers like `Cat.Nos Description` still count as alias headers when explicit Cat.No marker evidence exists, so right-side blocks on two-column spreads are mapped instead of dropped
34. Dual-role header cells that contain both alias and purchase evidence can emit `alias == purchase` mappings, allowing same-cell alphanumeric alias+MRP stacks such as `AC21104MB ... 306`
35. Continuation-row purchase salvage now accepts adjacent mapped MRP rows even when the continuation row still carries description text in the alias column or pack in the pack column, provided that continuation row has no strong alias of its own
36. Stream parser now handles shifted adjacent cells where one cell ends with an alias and the next cell begins with the MRP (for example `... \n0261 23` followed by `86160`), so right-block aliases are not dropped or mispaired
37. Stream alias normalization now applies numeric footnote cleanup for spaced Cat.Nos groups (for example `4122 831` -> `412283`), preventing flattened superscript markers from creating synthetic aliases
38. Strong alias salvage now rejects descriptive `WORD-<small number>` tokens (for example `SOCKET-3`, `WAY-1`, `MODULE-2`) so continuation description lines cannot become synthetic aliases when neighboring rows contain MRP values
39. Spaced-numeric alias-group detection now requires whole-token boundaries, so alphanumeric Cat.Nos like `5757 12PL` keep their suffix (`575712PL`) instead of being truncated to numeric-only aliases (`575712`)
40. Mapped purchase cells that inline MRP and pack in one value (for example `2220 1/10/100`) now parse the leading numeric token as MRP and treat the trailing slash token as pack, so those rows are not dropped
41. When mapped MRP is blank but description text ends with inline `MRP pack` (for example `... 1736 1/20/200`), extraction now prefers the inline MRP (`1736`) and avoids trailing pack suffix leakage (`200`) into purchase

This also covers mixed rows where several Cat.Nos are listed first but only the last one or two have visible MRP values (for example some variants are price-on-request while later variants have explicit MRP).

Implementation details for each case are documented in [docs/edge-cases.md](docs/edge-cases.md).

## Generalization Rules (No Single-PDF Hardcoding)

The extractor is designed to avoid one-off logic tied to a specific sample PDF.

Rules followed in code:
- No checks based on specific product families, brand names, or known catalog strings
- Role mapping is profile-driven (`alias/purchase/particulars/pack` synonyms), not page-id or file-name based
- Table parsing uses structural signals (line counts, token shapes, proximity, score thresholds), not fixed row positions
- Fallbacks are deterministic and reusable across vendors

Practical note:
- Per-PDF header profiles are allowed for synonym tuning, but core extraction logic remains generic.

## Backend options

### Option A: Camelot (default, free)
Best for:
- native/searchable PDFs
- clear table lines and structure
- whitespace/background-separated tables (via auto stream fallback)

Current behavior in this repo:
- Camelot runs in `auto` mode.
- It tries `lattice` first.
- If lattice finds no table (or only very narrow collapsed tables), it falls back to `stream` for that page.
- Stream fallback uses elevated `edge_tol` to keep section-separated sub-tables on the same page together.
- Additional structural normalization handles packed multiline rows and fragmented sparse matrices.

Set in .env:
EXTRACTION_BACKEND=camelot

### Option B: Document AI (paid)
Best for:
- scanned/image PDFs
- complex OCR-heavy layouts
- cases where PDF text extraction quality itself is poor (not just table boundary detection)

What is a "processor"?
- In Document AI, a processor is the hosted model endpoint that processes your files and returns a structured `Document` response.
- You create a processor once in Google Cloud, then call it by processor ID from this project.
- For this repository's current `docai` backend, use a processor that returns page tables (Form Parser is the safest default).

Set in .env:
EXTRACTION_BACKEND=docai
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us
GOOGLE_DOCAI_PROCESSOR_ID=your-processor-id
GOOGLE_DOCAI_PROCESSOR_VERSION=

### Document AI setup (step by step)

Follow these once per Google Cloud project:

1. Create/select a Google Cloud project and enable billing.
2. Enable the Document AI API.
3. Create a processor in Document AI console:
  - Recommended for this repo: Form Parser (table extraction + OCR).
  - Choose region matching your location setting (`us` or `eu`, etc.).
4. Create a service account and grant Document AI permissions.
5. Create and download a JSON key for that service account.
6. Save key securely on your machine (outside repo), then set:
  - `GOOGLE_APPLICATION_CREDENTIALS` = absolute path to JSON key.
7. Copy processor metadata from console:
  - Project ID -> `GOOGLE_CLOUD_PROJECT`
  - Processor ID -> `GOOGLE_DOCAI_PROCESSOR_ID`
  - Region -> `GOOGLE_CLOUD_LOCATION`
  - Optional processor version -> `GOOGLE_DOCAI_PROCESSOR_VERSION`
8. Update `.env`:

```env
EXTRACTION_BACKEND=docai
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/sa-key.json
GOOGLE_CLOUD_PROJECT=my-gcp-project
GOOGLE_CLOUD_LOCATION=us
GOOGLE_DOCAI_PROCESSOR_ID=1234567890abcdef
GOOGLE_DOCAI_PROCESSOR_VERSION=
```

9. Run a smoke test:

```bash
python scripts/extract_price_table.py \
  --input-pdf ./samples/sample_4.pdf \
  --target-page 46 \
  --output-xlsx /tmp/docai_test.xlsx \
  --backend docai
```

If credentials or processor config are missing, the script exits with a clear startup error.

Official docs used for setup:
- Document AI overview: https://docs.cloud.google.com/document-ai/docs/overview
- Processor types and capabilities: https://docs.cloud.google.com/document-ai/docs/processors-list
- Client-library setup and authentication: https://docs.cloud.google.com/document-ai/docs/process-documents-client-libraries
- Response structure (tables/forms/entities): https://docs.cloud.google.com/document-ai/docs/handle-response

## Why per-client/per-PDF mapping is a good idea

If each client usually keeps a stable catalog format, per-client mapping is cheaper and more stable than LLM inference.

Recommended strategy:
1. Keep deterministic mapping as default
2. Maintain client-specific synonym additions when needed
3. Use optional LLM only for rare ambiguous headers (not per row)

This keeps cost low and behavior predictable.

### Future concept: LLM-assisted profile inference (optional)

For future automation, you can infer profile JSON once per PDF using an LLM, but keep row extraction deterministic.

Proposed approach:
1. Extract first few candidate tables from the PDF.
2. Send only table headers + 1-2 sample rows to an LLM.
3. Ask LLM to produce `alias/purchase/particulars/pack` synonym profile JSON.
4. Save that JSON as `header_profiles/<pdf_stem>.json`.
5. Run extraction using that saved profile.

Cost remains low because LLM runs once per PDF profile generation, not per row.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Debian/Ubuntu if venv fails with ensurepip error:

```bash
sudo apt update
sudo apt install -y python3.12-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run extraction

```bash
python scripts/extract_price_table.py \
  --input-pdf ./samples/sample_1.pdf \
  --output-xlsx ./output/sample_1.xlsx \
  --verbose
```

Default run above extracts only `alias`, `purchase`, and `source_page`.

To include optional fields:

```bash
python scripts/extract_price_table.py \
  --input-pdf ./samples/sample_1.pdf \
  --output-xlsx ./output/sample_1_full.xlsx \
  --include-particulars \
  --include-pack
```

Useful options:

| Option | What it does | Default | Required? |
|---|---|---|---|
| `--input-pdf /path/to/file.pdf` | Source PDF to process. | None | **Yes** |
| `--output-xlsx /path/to/file.xlsx` | Output Excel path for extracted rows. | `output/extracted_prices.xlsx` | No |
| `--backend camelot\|docai` | Selects extraction backend (`camelot` for native PDFs, `docai` for scanned/complex PDFs). | Uses `EXTRACTION_BACKEND` env var if set, otherwise `camelot` | No |
| `--header-profile-file /absolute/path/to/profile.json` | Uses this exact header profile file for role mapping. | None (if omitted, auto-lookup is used and falls back to `<header-profile-dir>/default.json`) | No |
| `--header-profile-dir ./header_profiles` | Directory used for auto profile lookup (`<pdf_stem>.json`, then `default.json`). | `header_profiles` (so fallback is `header_profiles/default.json`) | No |
| `--min-page-score 2` | Minimum keyword triage score for selecting pages. | `2` | No |
| `--max-pages 20` | Limits number of candidate pages processed (useful for testing). `0` means no limit. | `0` | No |
| `--triage-keywords-file /path/to/weights.json` | Optional JSON to extend/override page triage keyword weights for different catalog vocabularies. | None | No |
| `--include-particulars` | Also extracts/exports `particulars`. Disabled by default to reduce compute. | Off | No |
| `--include-pack` | Also extracts/exports `pack`. Disabled by default to reduce compute. | Off | No |
| `--env-file /path/to/.env` | Loads environment variables from a specific `.env` file. | `.env` | No |
| `--verbose` | Enables debug-level logging and detailed progress logs. | Off | No |

Notes:
- `--year` is not implemented in the current script.
- Even though profile flags are optional, a valid header profile must be available at runtime by one of these routes:
  1. `--header-profile-file`
  2. `<header-profile-dir>/<input_pdf_stem>.json`
  3. `<header-profile-dir>/default.json`

## Parallel Processing for Large PDFs

Parallel behavior is configured centrally in `scripts/core/config.py` via defaults, and can be overridden by environment variables.

Default knobs in code:
- `DEFAULT_PARALLEL_ENABLED = True`
- `DEFAULT_EXTRACTION_MODE = process`
- `DEFAULT_EXTRACTION_WORKERS = min(16, cpu_count)`
- `DEFAULT_NORMALIZATION_MODE = off`
- `DEFAULT_NORMALIZATION_WORKERS = min(16, cpu_count)`
- `DEFAULT_MIN_PAGES_FOR_PARALLEL = 8`

Environment overrides (set in `.env`):
- `PARALLEL_PROCESSING_ENABLED=true|false`
- `PARALLEL_EXTRACTION_MODE=thread|process`
- `PARALLEL_EXTRACTION_WORKERS=<int>`
- `PARALLEL_NORMALIZATION_MODE=thread|process|off`
- `PARALLEL_NORMALIZATION_WORKERS=<int>`
- `PARALLEL_MIN_PAGES=<int>`

Example for a 300-page catalog:

```bash
PARALLEL_PROCESSING_ENABLED=true
PARALLEL_EXTRACTION_MODE=process
PARALLEL_EXTRACTION_WORKERS=16
PARALLEL_NORMALIZATION_MODE=off
PARALLEL_NORMALIZATION_WORKERS=16
PARALLEL_MIN_PAGES=8
```

Notes:
- Parallelization is page-level (tables/pages are still parsed deterministically).
- For very small inputs, processing stays effectively sequential due to the minimum-page threshold.
- `PARALLEL_EXTRACTION_MODE=process` can outperform threads on some CPUs because Camelot work is CPU-heavy.
- Normalization is usually lightweight; `PARALLEL_NORMALIZATION_MODE=off` is often fastest unless row volumes are very high.
- Current implementation uses CPU parallelism only. No GPU acceleration is used by Camelot, PyMuPDF, or the normalization logic.

## Quick Testing with `--target-page`

To extract a single page quickly (useful for debugging):

```bash
python scripts/extract_price_table.py \
  --input-pdf ./samples/sample_2.pdf \
  --target-page 6 \
  --output-xlsx ./output/page6_test.xlsx
```

- `--target-page` is 1-indexed (6 = page 6 in human numbering)
- Use this to iterate quickly on specific tables without reprocessing entire PDF
- Overrides page triage; extracts even if page wouldn't normally be selected

## Regression Testing

Run the full test suite:

```bash
pytest -q
```

If you are using the project virtual environment explicitly:

```bash
./.venv/bin/pytest -q
```

Run only the page-regression and workbook-truth tests:

```bash
pytest -q tests/test_target_page_regressions.py tests/test_full_workbook_regression.py
```

Locked truth-workbook regressions are included for `sample_2.pdf` and `sample_3.pdf`:

- Truth files:
  - `tests/truth/sample_2_truth.xlsx`
  - `tests/truth/sample_3_truth.xlsx`
- Test module:
  - `tests/test_full_workbook_regression.py`

These tests run a fresh extraction and compare the full `(alias, purchase, source_page)` output against the locked workbook rows to catch parser regressions early.

## Code Structure & Modularity

The extraction script is organized into focused, reusable modules for maintainability and readability.

### `scripts/core/` — Core Extraction Logic

Each module in `scripts/core/` is focused on a single responsibility and kept under ~200 lines for readability:

| Module | Responsibility | Key Exports |
|--------|---|---|
| `models.py` | Data structures, constants, regex patterns | `NormalizedRow`, `KEYWORD_WEIGHTS`, `ACTIVE_ROLE_SYNONYMS` |
| `parsing.py` | Price/alias/pack validation and cleaning | `parse_price()`, `clean_alias()`, `looks_like_alias()` |
| `text_utils.py` | Text cleaning, splitting, extraction | `split_cell_lines()`, `extract_alias_entries()`, `fallback_particulars()` |
| `quality_scoring.py` | Quality metrics for ranking rows/columns | `pack_column_quality()`, `normalized_row_quality()`, `select_pack_column()` |
| `role_markers.py` | Shared role marker matching from active profile synonyms | `has_role_marker()`, `infer_role_from_label()` |
| `header.py` | Header role detection and column mapping | `build_column_mappings()`, `infer_sparse_row_mappings()` |
| `table_analysis.py` | Layout detection (horizontal vs vertical tables) | `extract_horizontal_table_rows()` |
| `normalization.py` | Main row extraction & layout dispatch | `normalize_rows()`, `extract_packed_multiline_rows()`, `extract_sparse_rowwise_rows()` |
| `deduplication.py` | Two-layer row deduplication (by price, then by alias) | `deduplicate_rows()` |
| `export.py` | Excel output | `export_xlsx()` |
| `config.py` | Profile loading & role configuration | `load_profile()`, `configure_role_synonyms()` |
| `page_triage.py` | PDF page scoring & candidate selection | `page_score()`, `select_candidate_pages()` |

### `scripts/extract_price_table.py` — CLI Entry Point

- Thin entry point (~150 lines)
- Parses CLI arguments → orchestrates core modules → writes output
- No extraction logic; delegates to `core/normalization.py`, etc.

### `scripts/extractors/` — Backend Plugins

- `base.py`: TableExtractor interface
- `camelot_extractor.py`: Free native PDF extraction
- `docai_extractor.py`: Paid Google Document AI OCR

### Dependency Flow

```
models.py  ←  (all modules depend on constants/types)
↓
parsing.py, text_utils.py  ←  (foundational utilities)
↓
quality_scoring.py  ←  (uses parsing, text_utils)
header.py  ←  (uses parsing, quality_scoring, text_utils)
table_analysis.py  ←  (uses parsing)
↓
normalization.py  ←  (orchestrates all: header, table_analysis, quality_scoring)
deduplication.py  ←  (uses quality_scoring)
↓
extract_price_table.py  ←  (imports: config, normalization, dedup, export)
```

**No circular imports**: dependency graph is acyclic.

## Where to keep files

- Input PDFs: keep anywhere; pass path in `--input-pdf`.
- Output Excel: keep anywhere; pass path in `--output-xlsx`.
- Header profiles: keep in `header_profiles/` for auto-detection. Keep `default.json` there as the baseline fallback profile.

Recommended project layout:

```text
price-updater/
  samples/
    sinova_catalog.pdf
  header_profiles/
    default.json
  output/
    sample_1.xlsx
```

With that layout, this command uses `header_profiles/default.json` unless `header_profiles/sinova_catalog.json` also exists:

```bash
python scripts/extract_price_table.py \
  --input-pdf ./samples/sinova_catalog.pdf \
  --output-xlsx ./output/sinova_catalog_prices.xlsx \
  --verbose
```

## Logging

With --verbose, logs show:
- total pages and candidate pages
- pages skipped vs considered
- page numbers processed
- tables found per page
- rows extracted, deduplicated, and final count

## Camelot vs Document AI for Background-Color Separated Tables

Short answer:
- If the PDF is native/searchable and text is selectable, Camelot can usually handle many background-color separated tables using `stream` parsing and structural normalization.
- If the PDF is scanned, low-quality, or text ordering is unreliable, Document AI is usually better.

Notes from official docs:
- Camelot `lattice` is line-based; `stream` is whitespace/text-alignment based.
- Camelot supports options like `process_background=True`, `edge_tol`, and `row_tol` for difficult layouts.
- Document AI OCR is designed for complex document layout extraction and scanned documents, with configurable OCR/layout options.
