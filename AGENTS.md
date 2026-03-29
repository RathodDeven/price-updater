# AGENTS.md

Repository agent rules for `price-updater`.

## Mission
- Extract deterministic price rows from PDF tables into Excel.
- Prioritize correctness and traceability over aggressive guessing.

## Source of Truth
- Follow `README.md` for architecture, CLI behavior, and profile workflow.
- Keep extractor behavior aligned with output schema:
  - `particulars`
  - `alias`
  - `purchase`
  - `pack`
  - `source_page`

## Hard Constraints
- Do not add logic that targets one known PDF only.
- Do not use brand/product-family string checks as extraction gates.
- Do not rely on fixed page numbers, fixed row indexes, or fixed column positions.
- Keep alias + purchase mandatory; skip rows missing either.

## Preferred Strategies
- Use profile-driven header role mapping (`alias`, `purchase`, `particulars`, `pack`).
- Use structural heuristics:
  - token shape
  - line density
  - column proximity
  - quality scoring
- Keep fallback parsers generic for:
  - repeated header blocks
  - packed multiline rows
  - fragmented/sparse matrix rows

## Parsing Quality Rules
- Prefer full-match numeric parsing for prices.
- Prevent unit values (for example current/voltage forms) from becoming aliases.
- Prefer pack columns with pack-like evidence (slash forms, repeated pack tokens) over nearby numeric-only columns.
- When multiple candidates map to the same `(alias, purchase)`, keep the higher-quality row.

## Change Discipline
- Make smallest viable changes.
- Preserve public CLI options and existing output columns.
- Add or update logs when behavior changes materially.
- Update `README.md` whenever extraction behavior or edge-case support changes.
- Do not create summary/report markdown files (for example `REFACTORING.md`, `CHANGES.md`, `MIGRATION.md`) unless explicitly requested by the user.

## Verification Checklist
- Run extraction on representative sample(s).
- Check page/table row counts in logs.
- Spot-check at least one table each for:
  - standard header mapping
  - packed multiline fallback
  - fragmented table fallback
- Confirm no regressions in previously fixed pages.

## Supported Table Layouts
1. **Vertical Standard**: Headers in first row, product rows below (classic)
2. **Vertical Packed**: Multi-line product rows (configurations stacked within cells)
3. **Vertical Sparse**: Row-wise separated by backgrounds/borders, column fragments combined
4. **Horizontal Multi-Block**: Configurations as columns, roles (Reference, MRP, Pack) as rows
   - Detection: ≥2 role keywords in first column ("reference", "mrp", "unit price", "pack", "std pkg", etc.)
   - Iteration: Detects multiple product family blocks; each block gets independent role-label mapping
   - Block boundary: New block detected when "reference" role reappears after full prior block scanned
   - Validated on: sample_2 page 6 (Modular Plates with glossy/matte variants) → 20 rows ✓

## Quick Testing
- Use `--target-page N` CLI flag (1-indexed) to extract specific page only, bypassing triage
- Example: `python scripts/extract_price_table.py --input-pdf samples/sample_2.pdf --target-page 6 --output-xlsx output/test.xlsx`
- Useful for rapid iteration when debugging specific tables

## When Adding New Heuristics
- Express them as reusable rules, not dataset-specific patches.
- Document rationale in code with concise comments.
- Prefer score/ranking improvements over hard filters.
- If a heuristic could overfit, gate it behind generic evidence thresholds.

## Code Organization & Modularity

### File Size Rule
- **Maximum 200 lines per file** in `scripts/core/`
- Split logic into focused modules when files exceed this threshold
- Rationale: Files >200 lines become hard to read, debug, and test

### Module Organization
**`scripts/core/`** — Extraction logic (each module is single responsibility):
- `models.py` — Data structures, constants, regexes (NormalizedRow, KEYWORD_WEIGHTS)
- `parsing.py` — Price/alias/pack parsing & validation (~60 lines)
- `text_utils.py` — Text manipulation (split_cell_lines, extract_alias_entries, etc.)
- `quality_scoring.py` — Quality metrics & ranking (pack_column_quality, normalized_row_quality)
- `header.py` — Header detection & column mapping (build_column_mappings, infer_sparse_row_mappings)
- `table_analysis.py` — Table layout detection (extract_horizontal_table_rows)
- `normalization.py` — Row normalization dispatch (packed, sparse, vertical, horizontal)
- `deduplication.py` — Two-layer dedup logic (alias+price, then alias-only)
- `export.py` — Excel output (export_xlsx)
- `config.py` — Profile loading & role configuration (load_profile, configure_role_synonyms)
- `page_triage.py` — PDF scoring & page selection (page_score, select_candidate_pages)

**`scripts/extractors/`** — Backend table extraction plugins:
- `camelot_extractor.py` — Camelot (free, native PDF)
- `docai_extractor.py` — Document AI (paid, complex/scanned)
- `base.py` — TableExtractor interface

**`scripts/extract_price_table.py`** — Thin CLI entry point (~150 lines):
- parse_args(), main(), get_-extractor()
- Orchestrates core modules; no extraction logic

### Dependency Flow
```
models  ←  (all modules depend on constants/types)
parsing, text_utils  ←  (foundational utilities)
quality_scoring  ←  (depends on parsing, text_utils)
header  ←  (depends on parsing, quality_scoring, text_utils)
table_analysis  ←  (depends on parsing for validation)
normalization  ←  (ties all together: header, table_analysis, quality_scoring)
deduplication  ←  (depends on models, quality_scoring)
extract_price_table  ←  (import all: config, normalization, dedup, export)
```

### When to Split a File
If a file approaches 200 lines:
1. Identify cohesive sub-groups of functions
2. Extract to new `core/MODULE_name.py`
3. Update imports in dependent files
4. Run extraction tests to verify no regressions
5. Update this section to document new module

### Import Best Practices
- No circular imports (dependency graph is acyclic)
- Import at top of file; avoid inline imports except in edge cases
- Use absolute imports: `from core.parsing import parse_price` (not relative)
- Core modules do not import from extractors or scripts/

### Testing & Validation
- After modularizing new code, run full sample extractions
- Verify row counts match pre-refactor baselines
- Check zero Pylance errors: `pylance check scripts/`
- Profile impact of new modules on load time (should be negligible)

*** End Patch
