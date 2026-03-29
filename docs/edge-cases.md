# Edge Cases Handled

This document expands the short list in the main README and describes how each supported edge case is detected and handled.

## Edge-Case List

1. Repeated alias/price blocks in one table row
2. Header naming variation
3. Multiple logical sub-tables on one physical page
4. Empty, decorative, or spacing rows
5. Continuation text rows for particulars
6. Headerless packed multiline tables
7. Fragmented sparse matrices
8. Pack vs purchase ambiguity
9. False alias prevention
10. Split multi-row headers with pre-table bullets/labels
11. Pole labels leaking into alias column
12. Duplicate candidate rows from competing parsers
13. Shared purchase column across multiple reference columns
14. Compact horizontal tables collapsed into one dense text column
15. Reference codes with raised footnote markers
16. Two-column catalog spreads with repeated headers and paragraph blocks between header and item rows
17. Vertical dense-column tables with stacked alias and purchase in one merged column
18. Compact vertical blocks with separate Cat.Nos and stacked MRP/Pack in one column
19. Flattened accessory matrices with repeated alias-price token streams

## Handling Details

### 1. Repeated alias/price blocks in one table row

- Example: `Reference No.` and `Unit MRP` repeated for multiple product variants on the same row.
- Detection: multiple columns score strongly for `alias` and `purchase` in the same header band.
- Handling: the header mapper creates multiple mappings and pairs each alias block with the nearest valid purchase and pack columns.

### 2. Header naming variation

- Example: `Ref No`, `Reference No.`, `Cat.Nos`, `Item Code`, `MRP`, `Unit Price`.
- Detection: fuzzy scoring against active profile synonyms.
- Handling: role mapping remains profile-driven for `alias`, `purchase`, `particulars`, and `pack`.

### 3. Multiple logical sub-tables on one physical page

- Example: one page contains separate `Switch` and `Sockets` blocks.
- Detection: the extractor returns multiple matrices for one PDF page.
- Handling: each matrix is normalized independently, then merged and deduplicated.

### 4. Empty, decorative, or spacing rows

- Example: blank separators, brand strips, icon rows.
- Detection: rows lacking valid alias and purchase evidence.
- Handling: these rows are skipped before normalization output.

### 5. Continuation text rows for particulars

- Example: one row has alias and price, the next row only contains `(Indicator)` or similar description text.
- Detection: alias and purchase cells are empty while particulars text continues.
- Handling: sparse parsing appends the continuation text to the previous normalized row.

### 6. Headerless packed multiline tables

- Example: one physical row contains several logical rows separated by line breaks inside cells.
- Detection: multiline cells show repeated alias and price line signals without usable headers.
- Handling: the packed fallback expands line groups into row-wise records.

### 7. Fragmented sparse matrices

- Example: one logical table is split into sparse fragments across columns.
- Detection: row occupancy is sparse and logical values appear split across neighboring fragments.
- Handling: the parser can collapse fragments into a synthetic row before line-level extraction.

### 8. Pack vs purchase ambiguity

- Example: small integers like `10` or `20` can look like either pack or price.
- Detection: columns are evaluated by numeric distribution, token shape, and pack-like evidence.
- Handling: scoring prefers stable pack mappings when slash forms, pack hints, or pack distributions are stronger.

### 9. False alias prevention

- Example: `4 module` or `16A` getting treated as a product code.
- Detection: token shape checks plus unit-style rejection patterns.
- Handling: alias parsing only keeps code-like tokens and rejects non-code values.

### 10. Split multi-row headers with pre-table bullets/labels

- Example: bullet rows appear above the real table headers, while `Reference No` and `Unit MRP` are split over stacked rows.
- Detection: early rows are scanned and enriched across neighboring header lines.
- Handling: header detection picks the strongest header anchor instead of assuming the first non-empty row is the header.

### 11. Pole labels leaking into alias column

- Example: `1-pole` or `2-pole` being extracted as alias values.
- Detection: alias validation checks for pole-label patterns.
- Handling: pole labels are rejected so only code-like references remain eligible as aliases.

### 12. Duplicate candidate rows from competing parsers

- Example: sparse parsing finds the correct row and packed fallback emits a second mismatched version.
- Detection: multiple paths produce the same logical alias/purchase pair.
- Handling: fallback use is limited and dedup keeps the higher-quality row for each `(alias, purchase)` key.

### 13. Shared purchase column across multiple reference columns

- Example: two adjacent reference columns share one purchase column, while a later reference column maps to a later purchase column.
- Detection: alias/reference columns outnumber purchase columns in the same repeated structure.
- Handling: header-based and sparse inference both allow controlled purchase-column reuse when the structure supports it.

### 14. Compact horizontal tables collapsed into one dense text column

- Example: Camelot returns a single dense column containing multiline role labels, aliases, and prices.
- Detection: one dominant text column contains role markers and aligned alias/price sequences.
- Handling: the compact-horizontal parser pairs reference-role rows with the following purchase-role rows and expands them line by line.

### 15. Reference codes with raised footnote markers

- Example: visual superscripts like `5ST3010(1)` are flattened into `5ST30101)` by PDF extraction.
- Detection: alias parsing checks for a trailing footnote marker immediately before `)`.
- Handling: one trailing footnote marker is stripped so the exported alias keeps only the base reference code.

### 16. Two-column catalog spreads with repeated headers and paragraph blocks between header and item rows

- Example: one PDF page contains left and right catalog columns, each with repeated `Cat.Nos` and `MRP / Unit` headers, but those headers appear in the middle of the page (not near the top) and item rows start below feature text.
- Detection: repeated header groups are detected across page width above footer zones, with support for combined header tokens such as `MRP*/ /Unit` and layouts where `Description` may be absent.
- Handling: the Camelot backend re-extracts each detected header group as its own page region with dynamic column boundaries (purchase and pack required; particulars optional), then the vertical mapper uses enriched split headers so purchase and pack remain correctly separated.

### 17. Vertical dense-column tables with stacked alias and purchase in one merged column

- Example: the table body is collapsed so one column contains lines like `3 Pole 0270 39` followed by `3870`, while pack values remain in the next compact column.
- Detection: fallback scans columns for repeated catalog-number group patterns (for example `0270 39`) and requires enough row evidence before activating.
- Handling: dense-column parser extracts alias and purchase from the same cell, removes alias groups before price token fallback, and optionally reads pack from the nearest pack-like column.

### 18. Compact vertical blocks with separate Cat.Nos and stacked MRP/Pack in one column

- Example: one column contains `Cat.Nos` values while a nearby column stores multiline cells such as `15670`, `1`, `-`, `-`, `-` representing MRP and pack for 3P/4P blocks.
- Detection: fallback finds a strong alias column (`\d{3,6}\s\d{2,6}` pattern) and chooses the price/pack column by weighted evidence (parseable purchase, multiline structure, pack-like tokens), not just nearest numeric proximity.
- Handling: parser extracts alias from Cat.Nos column and purchase/pack from stacked lines, preventing rated-current columns or concatenated numeric streams from being treated as purchase.

### 19. Flattened accessory matrices with repeated alias-price token streams

- Example: rows/cells contain flattened sequences like `4210 12 3000 4210 13 3000 ...` where each alias-price pair belongs to a different accessory configuration.
- Detection: fallback scans both per-cell and full-row text for repeated `alias-group + price` patterns and enforces purchase bounds.
- Handling: parser expands every valid pair into a separate row, uses overlapping token scanning so later valid pairs are still recovered after earlier alias-only entries, and rejects malformed numeric streams (for example giant concatenations or leading-zero code fragments treated as price).

## Extraction Flow Summary

The table normalization flow is deterministic:

1. Try header-based mapping first.
2. If header mapping is weak, use sparse row-wise inference.
3. Merge continuation particulars rows before final validation.
4. Use packed multiline fallback only when structural evidence justifies it.
5. Enforce strict row validation: alias must be code-like and purchase must parse numerically.
6. Deduplicate by row quality when multiple parsers emit the same logical row.

This logic is generic and is intentionally not tied to fixed vendors, page numbers, or product names.