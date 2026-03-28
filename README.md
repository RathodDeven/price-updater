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

## How per-PDF header mapping works

The script now supports manual per-PDF header profiles.

Priority order used at runtime:
1. `--header-profile-file /path/to/profile.json` (explicit profile)
2. `header_profiles/<input_pdf_stem>.json` (auto-detected profile)
3. `header_profiles/default.json` (fallback)

Meaning:
- If input PDF is `samples/sinova_catalog.pdf`, script will auto-load `header_profiles/sinova_catalog.json` if it exists.
- If no per-PDF file exists, script loads `header_profiles/default.json`.
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

## Edge cases currently handled

1. Comparison tables (repeated header blocks)
- Example: Reference No + Unit MRP repeated twice in one table
- Handling: script detects multiple alias/purchase columns and creates separate mappings per block

2. Header naming variation
- Example: Ref No, Reference No., Cat.Nos, Item Code, MRP, Unit Price
- Handling: synonym dictionary + fuzzy matching

3. Multiple tables on same page
- Handling: each table is parsed independently and merged into one output set

4. Empty spacing between header and rows
- Handling: empty rows are ignored; only rows with valid values are kept

## Important limitation (current version)

Vertical/transposed product tables are not explicitly transposed by dedicated logic yet.
Depending on extraction output, some transposed layouts may parse partially or be skipped.

If you want strict handling for those, next step is to add a transposed-table detector in normalization.

## Backend options

### Option A: Camelot (default, free)
Best for:
- native/searchable PDFs
- clear table lines and structure

Set in .env:
EXTRACTION_BACKEND=camelot

### Option B: Document AI (paid)
Best for:
- scanned/image PDFs
- complex OCR-heavy layouts

Set in .env:
EXTRACTION_BACKEND=docai
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us
GOOGLE_DOCAI_PROCESSOR_ID=your-processor-id
GOOGLE_DOCAI_PROCESSOR_VERSION=

## Why per-client/per-PDF mapping is a good idea

Your intuition is correct: if each client usually keeps a stable catalog format, per-client mapping is cheaper and more stable than LLM inference.

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
  --input-pdf ./samples/vendor_catalog.pdf \
  --output-xlsx ./output/vendor_catalog_prices.xlsx \
  --verbose
```

Useful options:
- --backend camelot|docai
- --header-profile-file /absolute/path/to/profile.json
- --header-profile-dir ./header_profiles
- --min-page-score 2
- --max-pages 20
- --env-file /path/to/.env

## Where to keep files

- Input PDFs: keep anywhere; pass path in `--input-pdf`.
- Output Excel: keep anywhere; pass path in `--output-xlsx`.
- Header profiles: keep in `header_profiles/` for auto-detection.

Recommended project layout:

```text
price-updater/
  samples/
    sinova_catalog.pdf
  header_profiles/
    default.json
    sinova_catalog.json
  output/
    sinova_catalog_prices.xlsx
```

With that layout, this command auto-loads the profile:

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
