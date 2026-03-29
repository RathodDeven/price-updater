"""Shared role marker helpers sourced from active profile synonyms."""

from __future__ import annotations

from core.models import ACTIVE_ROLE_SYNONYMS
from core.text_utils import normalize_header


ROLE_MATCH_ORDER = ("alias", "purchase", "pack")


def role_markers(role: str, include_role_name: bool = False) -> list[str]:
    """Return normalized markers for a role from the active profile."""
    raw = list(ACTIVE_ROLE_SYNONYMS.get(role, []))
    if include_role_name:
        raw.append(role)

    out: list[str] = []
    seen: set[str] = set()
    for marker in raw:
        m = normalize_header(str(marker))
        if not m or m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def has_marker(text: str, marker: str) -> bool:
    """Check phrase-style marker match on normalized text.

    Matching uses space padding so single-word markers do not match partial
    tokens (for example, `rate` does not match `rated`).
    """
    t = f" {normalize_header(text)} "
    m = normalize_header(marker)
    if not m:
        return False
    return f" {m} " in t


def has_role_marker(text: str, role: str, include_role_name: bool = False) -> bool:
    """Return True if text contains any marker for the role."""
    return any(has_marker(text, marker) for marker in role_markers(role, include_role_name=include_role_name))


def role_marker_match_count(text: str, role: str, include_role_name: bool = False) -> int:
    """Count matched markers for a role in text."""
    return sum(1 for marker in role_markers(role, include_role_name=include_role_name) if has_marker(text, marker))


def infer_role_from_label(label: str) -> str | None:
    """Infer horizontal row role (`alias`, `purchase`, `pack`) from label."""
    scores = {
        role: role_marker_match_count(label, role, include_role_name=True)
        for role in ROLE_MATCH_ORDER
    }
    best_role = max(scores, key=scores.get)
    if scores[best_role] <= 0:
        return None

    tied_roles = [role for role, score in scores.items() if score == scores[best_role]]
    for role in ROLE_MATCH_ORDER:
        if role in tied_roles:
            return role
    return best_role
