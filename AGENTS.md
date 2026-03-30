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
- In stacked cells containing Cat.No + intermediate values + MRP (e.g. `4166 86\n18\n25\n4430`), take the LAST valid price (≥ MIN_PURCHASE), not the first — intermediate values are current ratings, not MRP.
- Alias group regex must use `[ \t]` (horizontal whitespace), NOT `\s`, to avoid cross-line matching that creates garbage aliases from adjacent lines (e.g. `7000\n4240` → `70004240`).
- When a cell contains a price-on-request marker (■/`\uf06e`/`\u25a0`), treat MRP as unavailable and skip the row.
- In mixed-layout cells, do not accept the concatenated text as an alias when no individual token qualifies — prevents description text becoming aliases.
- IP protection ratings (IP20, IP54, etc.) are not product codes.
- Fuzzy header matching must penalize very short normalized headers (< 3 chars) to prevent `(A)` → `"a"` from matching `"purchase"` or `"rate"` at 100%.
- Header enrichment uses `lookback_rows=4` to capture multi-row header labels (e.g. "MRP*" 2 rows above "Cat.Nos"), but header detection (`detect_header_row_index`) uses `lookback_rows=1` internally to keep anchor row stable.
- Header detection prefers earlier rows on near-ties (within 10 score points) since catalog headers appear at the top of tables.
- Backtick (`` ` ``) in headers should normalize to `inr` (same as `₹`), since some PDFs render the rupee symbol as a backtick.
- Fuzzy header matching must use word-boundary checks: `partial_ratio("rated current a", "rate")` = 100 is a false positive. Require the keyword to appear as a whole word in the header text (not a substring of a longer word). Matches that only come from fuzzy scoring without word-boundary evidence should be penalized (cap at 70% of raw score).
- When Camelot merges multiple PDF columns into one cell (dual-role columns), the merged column may contain both alias and purchase evidence (e.g. "Cat.Nos Pack MRP*/₹/Unit"). If no non-alias purchase candidate has purchase header evidence, allow dual-role alias columns with purchase evidence to serve as the purchase source for other alias columns.
- Header detection (`detect_header_row_index`) uses `lookahead_rows=0` so that a row ABOVE the actual header cannot steal role evidence from rows below it via lookahead. The actual enrichment after detection still uses `lookback_rows=4` and `lookahead_rows=2`.
- Stacked purchase cells (e.g. "15670\n1\n-\n-\n-" where MRP is first, followed by pack and 4P data) must be parsed by splitting on `\n` and taking the first valid price ≥ 50. `parse_price()` alone rejects these as multi-chunk. Fix is in `_extract_with_mappings` in `normalization.py`.
- When the main header-mapping path succeeds, compact_vertical rows are still merged in (not bypassed) so that stacked-cell pack values can upgrade pack-less rows from the header path.
- Garbage row prevention: reject rows where numerically `alias == purchase` during deduplication. This catches fragments from stacked cells (e.g., `"15670\n1\n-\n-\n-"` where a single line `"15670"` gets misclassified as both alias and purchase). Alias should be a product code; if alias and purchase are identical numeric values, it's an extraction error.
- Alias conflict safeguard: when the same alias appears with competing purchases, and at least one candidate is non-current-like, drop current-like purchases (16/25/40/.../250/320/400/630 etc.) before final alias-level selection. This prevents rated-current leakage (e.g., alias `669198` incorrectly keeping `250` instead of MRP `54260`).
- For mapped purchase cells containing stacked numeric lines (for example `65\n100\n14340\n1`), treat them as mixed current/MRP cells: collect all valid numeric candidates (>=50), prefer non-current-like values, and select the highest remaining candidate as purchase. This prevents Imin/Imax leakage into purchase for thermal-relay blocks (e.g., alias `416780` should keep `14340`, not `65`).
- In merged page matrices where a non-priced technical table (for example kVAr/current matrix with rows like `9 A`, `265 A`, `CTX...`) is embedded under a priced header block, skip those rows unless alias cell has explicit catalog-like grouping; do not extract kVAr/current values as purchase.
- For split rows where alias appears in particulars/description and MRP appears in the next row under mapped purchase column (e.g. `4174 08 ... 63 and 100` followed by `1700`), apply continuation-row purchase salvage before mixed-text numeric salvage. This prevents description numbers (like `63`) from being misread as purchase.
- Alias-cell stacked MRP override should not blindly replace mapped purchase when mapped purchase column has purchase-header evidence. Exception: when the same purchase column is shared by multiple alias mappings in one table (dual block layouts), allow alias-cell stacked pair override to resolve right-block MRP values (e.g., page 77), while keeping dedicated mapped MRP columns authoritative (e.g., page 94).
- When header-mapped extraction already yields rows, treat flattened stream rows as supplemental only; suppress current-like stream purchases during merge to avoid POR/missing-MRP leakage from nominal/current columns in mixed layouts (for example page 93 class issues).
- Flattened superscript footnotes in numeric Cat.Nos (e.g. `4122 76¹` extracted as `4122 761`) must normalize back to base alias (`412276`), not keep the marker digit in alias.
- In subsection rows where Camelot shifts columns (e.g. `Cat.No | - 18 | 10` under `No. of Ws | MRP | Pack`), treat pack-like mapped purchase tokens (`10`, `5/10`) as shifted pack and recover MRP from intermediate dash-prefixed numeric cell (`- 18`).
- In shifted rows where alias column repeats the previous Cat.No but particulars starts with a new Cat.No (e.g. alias `5734 50` vs particulars `5734 51 ...`), prefer the leading particulars alias when purchase is valid.

## Change Discipline
- Make smallest viable changes.
- Preserve public CLI options and existing output columns.
- Add or update logs when behavior changes materially.
- Update `README.md` whenever extraction behavior or edge-case support changes.
- Do not create temporary/debug files anywhere under the repository tree; use OS temp paths (for example `/tmp`) and clean them up.
- Do not create summary/report markdown files (for example `REFACTORING.md`, `CHANGES.md`, `MIGRATION.md`) unless explicitly requested by the user.
- Capture durable lessons from each debug iteration by updating AGENTS rules/checklists when a new recurring failure pattern is discovered.

## Verification Checklist
- Run extraction on representative sample(s).
- Check page/table row counts in logs.
- Spot-check at least one table each for:
  - standard header mapping
  - packed multiline fallback
  - fragmented table fallback
- Confirm no regressions in previously fixed pages.
- After each code generation/update cycle, check for editor/runtime errors in both `scripts/` and `tests/` before concluding.
- Run all tests before concluding changes:
  - `pytest -q`
  - or `./.venv/bin/pytest -q`
- If any file under `scripts/` is changed, running the full test suite is mandatory before responding.

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
