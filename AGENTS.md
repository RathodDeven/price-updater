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
- Alias-group detection for spaced numeric Cat.Nos must require token boundaries (for example `\b\d{3,6}[ \t]\d{2,6}\b`) so partial groups inside alphanumeric aliases (e.g. `5757 12PL`) are not truncated to numeric-only aliases (`575712`).
- When a cell contains a price-on-request marker (■/`\uf06e`/`\u25a0`), treat MRP as unavailable and skip the row.
- In mixed-layout cells, do not accept the concatenated text as an alias when no individual token qualifies — prevents description text becoming aliases.
- IP protection ratings (IP20, IP54, etc.) are not product codes.
- Fuzzy header matching must penalize very short normalized headers (< 3 chars) to prevent `(A)` → `"a"` from matching `"purchase"` or `"rate"` at 100%.
- Header enrichment uses `lookback_rows=4` to capture multi-row header labels (e.g. "MRP*" 2 rows above "Cat.Nos"), but header detection (`detect_header_row_index`) uses `lookback_rows=1` internally to keep anchor row stable.
- Header detection should scan deep enough to reach real table headers below long bullet/spec preambles (for example ~20 rows), while still using `lookback_rows=1` and `lookahead_rows=0` during anchor selection so early text rows do not inherit header evidence.
- Header detection prefers earlier rows on near-ties (within 10 score points) since catalog headers appear at the top of tables.
- Backtick (`` ` ``) in headers should normalize to `inr` (same as `₹`), since some PDFs render the rupee symbol as a backtick.
- Fuzzy header matching must use word-boundary checks: `partial_ratio("rated current a", "rate")` = 100 is a false positive. Require the keyword to appear as a whole word in the header text (not a substring of a longer word). Matches that only come from fuzzy scoring without word-boundary evidence should be penalized (cap at 70% of raw score).
- When Camelot merges multiple PDF columns into one cell (dual-role columns), the merged column may contain both alias and purchase evidence (e.g. "Cat.Nos Pack MRP*/₹/Unit"). If no non-alias purchase candidate has purchase header evidence, allow dual-role alias columns with purchase evidence to serve as the purchase source for other alias columns.
- Merged spread headers like `Cat.Nos Description` should still count as alias headers when explicit alias-marker evidence exists, even if particulars scores equally.
- If one header cell carries both alias and purchase evidence, allow a dual-role `alias == purchase` mapping and parse same-cell alphanumeric alias+MRP stacks generically.
- In dual-role numeric-only stacked cells under merged Cat.No+MRP headers (for example `77030\n423670` with pack in adjacent column), infer stack order from row-pattern consistency and keep alias/purchase orientation stable; do not emit reversed synthetic pairs (`77030 -> 423670`).
- Dual-role numeric stack inference should only apply to compact two-line stacks without in-cell pack tokens; mixed stacks like `63\n4\n10794\n1/5/60` must not infer `10794 -> 63` and should recover MRP from non-current numeric lines/continuation context.
- Do not reject mapped alias cells just because the description text contains current markers like `10A` or `16A` when the cell starts with a strong catalog alias.
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
- When recovering MRP from the end of particulars/description text, only accept a standalone trailing numeric token (e.g. `..., 32970`), not digits embedded inside a product code suffix (e.g. `5ST3070`).
- In mapped rows where purchase cell is pack-like (`1`, `5/10`, etc.) and alias is valid, check particulars/intermediate between-columns for a trailing standalone price and prefer it as MRP; treat purchase-cell token as shifted pack.
- In mapped purchase cells that inline MRP and pack in one token stream (for example `2220 1/10/100`), parse the leading numeric token as MRP and treat the trailing slash token as pack, instead of dropping the row.
- When mapped purchase is blank but particulars/intermediate text ends with inline `MRP pack` (for example `... 1736 1/20/200`), prefer the inline MRP (`1736`) before trailing-number fallback so pack suffix (`200`) is not misclassified as purchase.
- Nearby-column MRP recovery (when mapped purchase cell is blank) must only consider purchase-header-evidenced columns near alias, and should ignore candidates that are only current-like values.
- Nearby-column MRP recovery must stay on the same side of the alias as the mapped purchase column (right-side alias blocks must not borrow MRP from left-side parallel tables, and vice versa).
- In mapped continuation rows where the Cat.No sits at the start of particulars/description text (for example `AC21104MW ...`) and the mapped alias column is blank or stale, salvage that leading alias generically instead of dropping the row.
- Previous-row purchase salvage must prefer the mapped purchase column over alias-column numerics, and alias-column salvage should ignore text-bearing cells; otherwise continuation text like `... 625018` or nominal ratings like `60` can be misread as MRP.
- Continuation-row purchase salvage may use the adjacent mapped purchase value even when the continuation row still has description text in the alias column or pack in the pack column, as long as that continuation row has no strong alias of its own.
- In dual-role (`alias==purchase`) continuation layouts, purchase salvage should scan a short local window (not just immediate ±1 row) in the same mapped column and stop at strong split-alias boundaries, so split pairs like alias-on-row-N and MRP-on-row-(N-2) are recovered without crossing into other items.
- If a mapped purchase cell is blank while a separate pack column is populated, do not infer MRP from trailing description numerics by default; values like `Pack consisting of 100` are pack/quantity text, not price.
- When emitting multiple aliases from one mapped multiline alias cell, only emit extra line-level aliases that are strong catalog-code candidates; configuration text like `2 NO`, `2 NC`, `4 NO`, `1 NC` must not become aliases.
- Stream fallback must handle shifted adjacent-cell pairs where an alias appears at the tail of one cell and the MRP appears at the head of the next cell (for example `... 0261 23` then `86160`) so right-block aliases are not dropped or mispaired to previous MRPs.
- Stream alias-group normalization must apply numeric footnote cleanup for spaced Cat.Nos groups (for example `4122 831` -> `412283`) to prevent flattened superscript markers from producing synthetic aliases like `4122831`.
- Strong alias salvage must reject descriptive word-number tokens (for example `SOCKET-3`, `WAY-1`, `MODULE-2`) so continuation description fragments with adjacent MRPs cannot be promoted to aliases.
- Strong alias candidates must be digit-dense (>=3 digits) so mixed description blends like `IP43MIVAN` are not emitted as aliases.
- Spaced numeric aliases with suffix letters must preserve the suffix even when OCR inserts a space before it (for example `5078 86 N` -> `507886N`).
- Split multiline numeric aliases with short prefix lines must be rejoined before validation (for example `5` + `078 60` -> `507860`), and shorter trailing fragments (`07860`) must not be emitted as extra aliases.
- When mapped alias is overridden from particulars (because alias column is blank/stale), suppress extra alias-line expansion from the stale alias cell to avoid emitting conflicting alias+price pairs.

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
- **Preferred target: <= 400 lines per file**
- **Hard cap: 500 lines per file** (including files outside `scripts/core/`)
- For `scripts/core/`, keep files as small as practical (often around 200-300 lines) by splitting cohesive logic into focused modules
- Rationale: very large files are harder to reason about, debug, and regression-test

### Folder Architecture Rule
- Keep related implementation files in the same domain folder (for example normalization-related logic under `scripts/core/` in dedicated modules)
- Avoid mixing unrelated responsibilities into one large file; split by concern and wire through imports
- For similar service-style logic, create grouped modules inside the relevant folder rather than scattering helpers across distant directories

### Module Organization
**`scripts/core/`** — Extraction logic (each module is single responsibility):
- `models.py` — Data structures, constants, regexes (NormalizedRow, KEYWORD_WEIGHTS)
- `parsing.py` — Price/alias/pack parsing & validation (~60 lines)
- `text_utils.py` — Text manipulation (split_cell_lines, extract_alias_entries, etc.)
- `quality_scoring.py` — Quality metrics & ranking (pack_column_quality, normalized_row_quality)
- `header.py` — Header detection & column mapping (build_column_mappings, infer_sparse_row_mappings)
- `table_analysis.py` — Table layout detection (extract_horizontal_table_rows)
- `normalization.py` — Row normalization dispatch/orchestration
- `normalization_mapped.py` — Header-mapped row extraction
- `normalization_fallbacks.py` — Sparse/packed fallback extractors
- `normalization_helpers.py` — Shared normalization utilities
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
If a file approaches 400 lines (or starts mixing concerns):
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
