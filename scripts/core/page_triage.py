"""Page triage and PDF processing utilities."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import fitz

from core.models import ACTIVE_ROLE_SYNONYMS, ACTIVE_TRIAGE_ROLE_MARKERS, ACTIVE_TRIAGE_ROLE_WEIGHTS

logger = logging.getLogger(__name__)


# Structural hints: code-like aliases and price-like values indicate table pages.
ALIAS_TOKEN_PATTERN = re.compile(r"\b(?=[A-Za-z0-9\-_/\.]{5,}\b)(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9\-_/\.]*\b")
PRICE_TOKEN_PATTERN = re.compile(r"\b\d{2,5}(?:\.\d+)?(?:\.-)?\b")


def page_score(text: str, keyword_weights: dict[str, int]) -> int:
    """Score a page based on keyword presence for triage."""
    t = text.lower()
    score = 0
    for kw, weight in keyword_weights.items():
        if kw in t:
            score += weight
    return score


def _role_coverage(text_lower: str) -> int:
    """Count how many role groups are present on the page."""
    covered = 0
    for markers in ACTIVE_TRIAGE_ROLE_MARKERS.values():
        if any(marker in text_lower for marker in markers):
            covered += 1
    return covered


def _table_signal_counts(text: str) -> tuple[int, int]:
    """Return counts of alias-like and price-like tokens in page text."""
    aliases = len(ALIAS_TOKEN_PATTERN.findall(text))
    prices = len(PRICE_TOKEN_PATTERN.findall(text))
    return aliases, prices


def is_table_like_page(text: str, score: int, min_score: int) -> bool:
    """Gate candidate pages using both keyword score and structural evidence.

    This prevents broad keyword matches (e.g. cover/index pages) from becoming
    candidates while keeping pages with dense table signals.
    """
    if score < min_score:
        return False

    text_lower = text.lower()
    role_count = _role_coverage(text_lower)
    alias_count, price_count = _table_signal_counts(text)
    alias_markers = ACTIVE_TRIAGE_ROLE_MARKERS.get("alias", [])
    purchase_markers = ACTIVE_TRIAGE_ROLE_MARKERS.get("purchase", [])
    has_alias_marker = any(marker in text_lower for marker in alias_markers)
    has_purchase_marker = any(marker in text_lower for marker in purchase_markers)

    if not (has_alias_marker and has_purchase_marker):
        return False

    # Strong role evidence (typical table pages)
    if role_count >= 3:
        return True

    # Moderate role evidence + structural signals
    if role_count >= 2 and alias_count >= 6 and price_count >= 6:
        return True

    # Very strong structural evidence even with fewer role labels
    if alias_count >= 20 and price_count >= 20:
        return True

    return False


def build_profile_keyword_weights() -> dict[str, int]:
    """Build triage keyword weights from active role synonyms.

    This avoids hardcoding vendor terms and adapts to the loaded profile.
    """
    weights: dict[str, int] = {}
    for role, synonyms in ACTIVE_ROLE_SYNONYMS.items():
        role_weight = ACTIVE_TRIAGE_ROLE_WEIGHTS.get(role, 1)
        for synonym in synonyms:
            key = " ".join(str(synonym).lower().split()).strip()
            if not key:
                continue
            current = weights.get(key, 0)
            if role_weight > current:
                weights[key] = role_weight

    # Ensure role names themselves are considered even if missing in synonyms.
    for role, role_weight in ACTIVE_TRIAGE_ROLE_WEIGHTS.items():
        current = weights.get(role, 0)
        if role_weight > current:
            weights[role] = role_weight

    return weights


def load_keyword_weights(config_file: Path | None = None) -> dict[str, int]:
    """Load triage keyword weights from optional JSON file.

    File format:
    {
      "reference": 2,
      "mrp": 3,
      "custom_keyword": 1
    }
    """
    merged = build_profile_keyword_weights()
    if config_file is None:
        return merged

    payload = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Triage keyword config must be a JSON object: {config_file}")

    for key, value in payload.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if not isinstance(value, int):
            continue
        merged[key.strip().lower()] = value
    return merged


def select_candidate_pages(
    pdf_path: Path,
    min_score: int = 2,
    keyword_weights: dict[str, int] | None = None,
) -> tuple[list[int], dict[int, int]]:
    """Identify pages likely to contain price tables using keyword heuristics.
    
    Returns (candidate_page_indices, page_scores_map).
    Candidate pages are 0-indexed. Scores are returned for all pages.
    """
    doc: Any = fitz.open(pdf_path)
    page_scores: dict[int, int] = {}
    candidates: list[int] = []

    logger.info(f"Starting page triage on {pdf_path.name}")
    logger.debug(f"Minimum page score threshold: {min_score}")
    active_weights = keyword_weights or build_profile_keyword_weights()
    logger.debug(f"Using {len(active_weights)} triage keywords")

    for i in range(doc.page_count):
        text = doc.get_page_text(i)
        score = page_score(text, active_weights)
        page_scores[i] = score
        if is_table_like_page(text, score, min_score):
            candidates.append(i)
            logger.debug(f"Page {i + 1}: score={score} (CANDIDATE)")
        else:
            logger.debug(f"Page {i + 1}: score={score} (skipped)")

    if not candidates:
        logger.warning("No pages matched keyword threshold. Selecting top 30 pages by score.")
        candidates = sorted(page_scores.keys(), key=lambda k: page_scores[k], reverse=True)[: min(30, len(page_scores))]
        logger.info(f"Top {len(candidates)} pages selected by score")

    doc.close()
    candidates = sorted(set(candidates))

    candidate_pages_human = [p + 1 for p in candidates]

    logger.info(f"Page triage complete: {len(candidates)} candidate pages out of {len(page_scores)} total")
    logger.info(f"Candidate page numbers (0-index): {candidates}")
    logger.info(f"Candidate page numbers (human): {candidate_pages_human}")

    return candidates, page_scores


def build_single_page_pdf_bytes(pdf_path: Path, page_index: int) -> bytes:
    """Extract a single page from PDF and return as bytes."""
    src = fitz.open(pdf_path)
    single = fitz.open()
    single.insert_pdf(src, from_page=page_index, to_page=page_index)
    data = single.tobytes(garbage=True, deflate=True)
    single.close()
    src.close()
    return data
