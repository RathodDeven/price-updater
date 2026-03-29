# Price Updater - PDF Table Extractor

This repository contains a Python script that extracts price rows from vendor PDFs into Excel.

Primary script:
- scripts/extract_price_table.py

Output columns:
- particulars (description, if available)
- alias (product id / reference no / item code)
- purchase (MRP / unit price)
- pack (optional)
- source_page

## Is any LLM used?

No. The current script does not call OpenAI or any other LLM.

Current flow is fully deterministic:
1. Page triage with keyword scoring
2. Table extraction backend (Camelot or Document AI)
3. Header-to-role mapping with synonym + fuzzy matching
4. Row validation and export

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

Then each extracted row is normalized to output columns:
- `particulars`
- `alias`
- `purchase`
- `pack`
- `source_page`

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

## Edge Cases Currently Handled (Detailed)

1. Repeated alias/price blocks in one table row
- Example: `Reference No.` + `Unit MRP` repeated for WHITE and GREY variants on the same row.
- Handling: header mapper creates multiple role mappings and pairs each alias column with the nearest purchase column.

2. Header naming variation
- Example: Ref No, Reference No., Cat.Nos, Item Code, MRP, Unit Price.
- Handling: profile synonyms + fuzzy scoring for `alias/purchase/particulars/pack` roles.

3. Multiple logical sub-tables on one physical page
- Example: `Switch` block followed by `Sockets` block on the same page.
- Handling: Camelot auto-mode falls back to `stream` with high `edge_tol` so separated blocks are captured in one pass, then normalized together.

4. Empty spacing and decorative rows
- Example: brand strips, icon rows, blank separators.
- Handling: rows without required role evidence are ignored.

5. Continuation text rows for particulars
- Example: one row has alias+price, next row has only `(Indicator)` or `(LED Indicator)` in description.
- Handling: sparse parser merges continuation text into the previous product row before validation.

6. Headerless packed multiline tables
- Example: one physical row where each cell contains multiple logical values separated by newlines.
- Handling: packed fallback parser expands line groups into row-wise records.

7. Fragmented sparse matrices
- Example: one logical table split into sparse matrix fragments.
- Handling: parser can collapse fragments into a synthetic row and then apply line-level extraction.

8. Pack vs purchase ambiguity
- Example: small integers (10/20) can look like either pack or price in noisy matrices.
- Handling: role scoring uses numeric distribution and pack-strength evidence (slash forms, token shape, co-occurrence) to prefer stable mappings.

9. False alias prevention
- Example: `4 module` or `16A` being mistaken for product code.
- Handling: alias detection enforces code-like token shape and filters unit-like patterns.

10. Duplicate candidate rows from competing parsers
- Example: sparse parser extracts correct rows, packed parser also emits mismatched alias/price rows.
- Handling: packed fallback is only used when sparse extraction is weak, and final dedup keeps higher-quality rows per `(alias, purchase)` key.

11. Shared purchase column across multiple reference columns
- Example: two adjacent reference columns share one purchase column, while a later reference column maps to a later purchase column.
- Handling: both header-based and sparse inference mapping allow purchase-column reuse when reference columns outnumber purchase columns, so each reference gets its own row even when sharing price.
- Generic behavior: does not depend on color labels (WHITE/GREY/BLACK); it relies on column structure and proximity.

12. Compact horizontal tables collapsed into one dense text column
- Example: horizontal layout extracted by Camelot as one column where each cell contains multiline aliases/prices/role labels.
- Handling: compact-horizontal parser pairs `Reference No.` rows with following `Unit MRP` rows and expands line-wise aliases/prices.

## Edge-Case Handling Details (By Case)

1. Repeated alias/price blocks in one table row
- Detection: multiple columns score strongly for `alias` and `purchase` in the same header band.
- Handling: create multiple mappings and pair nearest role columns per block.

2. Header naming variation
- Detection: fuzzy scoring against profile synonyms.
- Handling: profile-driven role mapping (`alias/purchase/particulars/pack`) with fallback thresholds.

3. Multiple logical sub-tables on one page
- Detection: extractor returns separate matrices for one PDF page.
- Handling: normalize each matrix independently, then merge and deduplicate.

4. Decorative/spacing rows
- Detection: rows lacking valid alias+purchase evidence.
- Handling: skip early before normalization output.

5. Continuation particulars rows
- Detection: alias/purchase columns empty while particulars column has text.
- Handling: append continuation text to previous row particulars.

6. Headerless packed multiline matrices
- Detection: multiline cells with repeated alias/price line signals.
- Handling: expand lines into logical rows and align by line index.

7. Fragmented sparse matrices
- Detection: sparse row occupancy with split column fragments.
- Handling: optionally collapse fragments into synthetic row, then parse.

8. Pack vs price ambiguity
- Detection: columns dominated by small integers or mixed token shapes.
- Handling: pack-strength scoring (slash forms, hints, co-occurrence) to prefer stable pack mapping.

9. False alias prevention
- Detection: token shape checks and unit-like regex guards.
- Handling: reject non-code-like values from alias role.

10. Cross-parser duplicate candidates
- Detection: same logical row emitted by competing fallback paths.
- Handling: quality-ranked dedup by `(alias, purchase)`, then alias uniqueness layer.

11. Shared purchase columns for multiple references
- Detection: alias/reference columns outnumber purchase columns in the same structure.
- Handling: allow purchase-column reuse (nearest mapping) in header and sparse inference so all references produce rows with shared purchase where applicable.

12. Compact horizontal single-column collapse
- Detection: role markers and multiline alias/price sequences inside one dominant column.
- Handling: pair reference-role rows with following purchase-role rows and expand aliases/prices line-wise.

## How edge-case handling works (step by step)

This is the deterministic decision path used per extracted table matrix:

1. Try header-based mapping first.
- If strong header roles are found, parse rows directly from mapped columns.

2. If header mapping is weak, run sparse row-wise inference.
- Infer alias/purchase/pack/particulars columns from value-shape evidence and co-occurrence.

3. Before sparse extraction, merge continuation description rows.
- Rows with empty alias+purchase but non-empty particulars are appended to the previous row description.

4. Use packed multiline fallback only when sparse evidence is insufficient.
- Prevents spurious rows when sparse parsing already confidently extracted records.

5. Apply strict row validation.
- `alias` must be code-like.
- `purchase` must parse as numeric.
- Rows failing either rule are skipped.

6. Resolve duplicates by quality.
- For the same `(alias, purchase)`, keep the row with stronger pack/particulars quality.

This flow is intentionally generic and avoids vendor- or page-specific hardcoding.

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

Set in .env:
EXTRACTION_BACKEND=docai
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us
GOOGLE_DOCAI_PROCESSOR_ID=your-processor-id
GOOGLE_DOCAI_PROCESSOR_VERSION=

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
| `--env-file /path/to/.env` | Loads environment variables from a specific `.env` file. | `.env` | No |
| `--verbose` | Enables debug-level logging and detailed progress logs. | Off | No |

Notes:
- `--year` is not implemented in the current script.
- Even though profile flags are optional, a valid header profile must be available at runtime by one of these routes:
  1. `--header-profile-file`
  2. `<header-profile-dir>/<input_pdf_stem>.json`
  3. `<header-profile-dir>/default.json`

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
