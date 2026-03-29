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

## Verification Checklist
- Run extraction on representative sample(s).
- Check page/table row counts in logs.
- Spot-check at least one table each for:
  - standard header mapping
  - packed multiline fallback
  - fragmented table fallback
- Confirm no regressions in previously fixed pages.

## When Adding New Heuristics
- Express them as reusable rules, not dataset-specific patches.
- Document rationale in code with concise comments.
- Prefer score/ranking improvements over hard filters.
- If a heuristic could overfit, gate it behind generic evidence thresholds.
