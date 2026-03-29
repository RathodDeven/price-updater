"""Row deduplication logic."""

from __future__ import annotations

from core.models import NormalizedRow
from core.quality_scoring import normalized_row_quality


def deduplicate_rows(rows: list[NormalizedRow]) -> list[NormalizedRow]:
    """Remove duplicate rows using two-layer deduplication strategy.
    
    Layer 1: Deduplicate by (alias, purchase) key.
             When same alias+price appears multiple times, keep highest quality.
    
    Layer 2: Enforce global alias uniqueness.
             Each alias appears at most once. If multiple prices exist for same alias,
             keep the highest-quality candidate (prioritizes better pack/particulars).
    
    This enforces the business rule: alias values are globally unique identifiers.
    """
    # Layer 1: Deduplicate by (alias, purchase) key
    best_by_key: dict[tuple[str, float], NormalizedRow] = {}
    for row in rows:
        key = (row.alias, row.purchase)
        current = best_by_key.get(key)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_key[key] = row

    # Layer 2: Enforce alias uniqueness
    best_by_alias: dict[str, NormalizedRow] = {}
    for row in best_by_key.values():
        current = best_by_alias.get(row.alias)
        if current is None or normalized_row_quality(row) > normalized_row_quality(current):
            best_by_alias[row.alias] = row

    return list(best_by_alias.values())
