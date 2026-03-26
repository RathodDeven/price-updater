# Price Updater Backend Starter

Starter backend for an electrical-distributor price update product.

## What this repo does

- Accepts a vendor PDF and distributor Excel file
- Renders PDF pages to images
- Extracts native PDF text
- Runs a **primary document extractor** (Google Document AI placeholder)
- Runs an **OpenAI fallback extractor** on pages that primary extraction could not resolve
- Validates extracted rows
- Matches extracted product codes against Excel `Alias`
- Exports review files and an updated workbook

## Architecture

1. `pdf_service.py`
   - Reads PDF pages
   - Renders page images
   - Extracts native text
2. `google_docai.py`
   - Primary structured document extraction layer
3. `openai_fallback.py`
   - Fallback page extractor using image + text input
4. `validator.py`
   - Rejects weak rows
5. `matcher.py`
   - Exact code match against Excel Alias
6. `exporter.py`
   - Writes CSVs and review workbook
7. `pipeline.py`
   - Orchestrates the full flow

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open docs at `http://127.0.0.1:8000/docs`

## Main endpoint

`POST /v1/process`

Form fields:
- `pdf_file`
- `excel_file`

Returns a JSON summary with output paths.

## Important

This repo is designed so that **API credentials are the only thing missing**.
The structure, models, pipeline, validation, matching, and exports are already wired.

## Env vars you will need

- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `GOOGLE_APPLICATION_CREDENTIALS`
- `GOOGLE_CLOUD_PROJECT`
- `GOOGLE_CLOUD_LOCATION`
- `GOOGLE_DOCAI_PROCESSOR_ID`

## Notes for the developer

- The Google Document AI processor wiring is in `app/services/google_docai.py`
- The OpenAI fallback is in `app/services/openai_fallback.py`
- The JSON schema for fallback extraction is in `app/models/schemas.py`
- The acceptance rules are in `app/services/validator.py`

