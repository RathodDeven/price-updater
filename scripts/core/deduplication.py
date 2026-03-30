"""Row deduplication logic."""

from __future__ import annotations

from core.models import NormalizedRow
from core.quality_scoring import normalized_row_quality


# Common electrical current ratings that often leak into purchase by mistake.
CURRENT_LIKE_PURCHASES = {
    0.1,
    0.16,
    0.25,
    0.4,
    0.63,
    1.0,
    1.6,
    2.5,
    4.0,
    5.0,
    6.0,
    7.0,
    9.0,
    10.0,
    12.0,
    16.0,
    18.0,
    20.0,
    22.0,
    25.0,
    28.0,
    32.0,
    40.0,
    50.0,
    54.0,
    63.0,
    70.0,
    80.0,
    100.0,
    125.0,
    160.0,
    200.0,
    250.0,
    320.0,
    400.0,
    630.0,
    800.0,
    1000.0,
}


def _is_current_like_purchase(value: float) -> bool:
    return round(float(value), 2) in CURRENT_LIKE_PURCHASES


def deduplicate_rows(rows: list[NormalizedRow]) -> list[NormalizedRow]:
    """Remove duplicate rows using two-layer deduplication strategy.
    
    Layer 1: Deduplicate by (alias, purchase) key.
             When same alias+price appears multiple times, keep highest quality.
             Also reject rows where alias == purchase numerically (garbage from
             stacked cell fragments where purchase value was misclassified as alias).
    
    Layer 2: Enforce global alias uniqueness.
             Each alias appears at most once. If multiple prices exist for same alias,
             keep the highest-quality candidate (prioritizes better pack/particulars).
    
    This enforces the business rule: alias values are globally unique identifiers.
    """
    # Layer 1: Deduplicate by (alias, purchase) key
    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in rows:
        # Reject rows where alias and purchase are the same numeric value.
        # This catches garbage from stacked cells (e.g. "15670\n1\n-\n-\n-" 
        # where "15670" line gets extracted as both alias and purchase).
        try:
            alias_num = float(row.alias)
            if alias_num == row.purchase:
                continue
        except (ValueError, TypeError):
            pass  # alias is not numeric, keep it
        
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row

    # Layer 2: Enforce alias uniqueness
    by_alias: dict[str, list[NormalizedRow]] = {}
    for row in best_by_key.values():
        by_alias.setdefault(row.alias, []).append(row)

    best_by_alias: dict[str, NormalizedRow] = {}
    for alias, candidates in by_alias.items():
        non_current_like = [r for r in candidates if not _is_current_like_purchase(r.purchase)]
        # If alias has both current-like and non-current-like purchases,
        # prefer non-current-like candidates (current leakage safeguard).
        pool = non_current_like if non_current_like else candidates
        best_by_alias[alias] = max(pool, key=normalized_row_quality)

    return list(best_by_alias.values())
