"""Configuration and profile loading."""

from __future__ import annotations

import os
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from core.models import (
    ACTIVE_ROLE_SYNONYMS,
    ACTIVE_TRIAGE_ROLE_MARKERS,
    ACTIVE_TRIAGE_ROLE_WEIGHTS,
    REQUIRED_PROFILE_ROLES,
)

logger = logging.getLogger(__name__)


# Central defaults for page triage role influence.
DEFAULT_TRIAGE_ROLE_WEIGHTS: dict[str, int] = {
    "alias": 2,
    "purchase": 3,
    "pack": 2,
    "particulars": 2,
}


@dataclass(frozen=True)
class ParallelProcessingConfig:
    """Runtime parallelization config for extraction pipeline."""

    enabled: bool
    extraction_mode: Literal["thread", "process"]
    extraction_workers: int
    normalization_mode: Literal["thread", "process", "off"]
    normalization_workers: int
    min_pages_for_parallel: int


DEFAULT_PARALLEL_ENABLED = True
DEFAULT_EXTRACTION_MODE: Literal["thread", "process"] = "process"
DEFAULT_NORMALIZATION_MODE: Literal["thread", "process", "off"] = "off"
DEFAULT_MIN_PAGES_FOR_PARALLEL = 8
DEFAULT_EXTRACTION_WORKERS = max(1, min(16, os.cpu_count() or 4))
DEFAULT_NORMALIZATION_WORKERS = max(1, min(16, os.cpu_count() or 4))


def _env_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _env_extraction_mode(raw: str | None, default: Literal["thread", "process"]) -> Literal["thread", "process"]:
    if raw is None:
        return default
    mode = raw.strip().lower()
    if mode in {"thread", "process"}:
        return cast(Literal["thread", "process"], mode)
    return default


def _env_normalization_mode(
    raw: str | None,
    default: Literal["thread", "process", "off"],
) -> Literal["thread", "process", "off"]:
    if raw is None:
        return default
    mode = raw.strip().lower()
    if mode in {"thread", "process", "off"}:
        return cast(Literal["thread", "process", "off"], mode)
    return default


def load_parallel_processing_config(env: dict[str, str] | None = None) -> ParallelProcessingConfig:
    """Load parallel processing settings from environment with code defaults."""
    source = env or os.environ
    enabled = _env_bool(source.get("PARALLEL_PROCESSING_ENABLED"), DEFAULT_PARALLEL_ENABLED)
    extraction_mode = _env_extraction_mode(source.get("PARALLEL_EXTRACTION_MODE"), DEFAULT_EXTRACTION_MODE)
    extraction_workers = _env_int(source.get("PARALLEL_EXTRACTION_WORKERS"), DEFAULT_EXTRACTION_WORKERS)
    normalization_mode = _env_normalization_mode(
        source.get("PARALLEL_NORMALIZATION_MODE"),
        DEFAULT_NORMALIZATION_MODE,
    )
    normalization_workers = _env_int(source.get("PARALLEL_NORMALIZATION_WORKERS"), DEFAULT_NORMALIZATION_WORKERS)
    min_pages = _env_int(source.get("PARALLEL_MIN_PAGES"), DEFAULT_MIN_PAGES_FOR_PARALLEL)
    return ParallelProcessingConfig(
        enabled=enabled,
        extraction_mode=extraction_mode,
        extraction_workers=extraction_workers,
        normalization_mode=normalization_mode,
        normalization_workers=normalization_workers,
        min_pages_for_parallel=min_pages,
    )


def load_profile(path: Path) -> dict[str, list[str]]:
    """Load and validate header profile JSON.
    
    Profile must contain lists for each required role:
    - alias
    - purchase  
    - particulars
    - pack
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Profile must be a JSON object: {path}")

    profile: dict[str, list[str]] = {}
    for role in REQUIRED_PROFILE_ROLES:
        synonyms = payload.get(role)
        if not isinstance(synonyms, list):
            raise ValueError(f"Role '{role}' must be a list in profile: {path}")
        cleaned = [str(s).strip() for s in synonyms if str(s).strip()]
        if not cleaned:
            raise ValueError(f"Role '{role}' list is empty in profile: {path}")
        profile[role] = cleaned

    return profile


def build_role_markers(role_synonyms: dict[str, list[str]]) -> dict[str, list[str]]:
    """Build triage marker lists directly from role synonyms.

    This keeps triage and header mapping aligned and avoids duplicate marker
    maintenance in profile files.
    """
    role_markers: dict[str, list[str]] = {}
    for role, synonyms in role_synonyms.items():
        seen: set[str] = set()
        normalized: list[str] = []
        for synonym in synonyms:
            marker = " ".join(str(synonym).lower().split()).strip()
            if not marker or marker in seen:
                continue
            seen.add(marker)
            normalized.append(marker)
        role_markers[role] = normalized
    return role_markers


def load_profile_triage_config(path: Path) -> dict[str, int]:
    """Load optional triage role weights from profile.

    Supported optional profile block:
    {
      "triage": {
        "role_weights": {"alias": 2, "purchase": 3, "pack": 2, "particulars": 2}
      }
    }
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    triage_payload = payload.get("triage", {}) if isinstance(payload, dict) else {}

    role_weights = dict(DEFAULT_TRIAGE_ROLE_WEIGHTS)

    if isinstance(triage_payload, dict):
        weights_payload = triage_payload.get("role_weights")
        if isinstance(weights_payload, dict):
            for role, value in weights_payload.items():
                if role in REQUIRED_PROFILE_ROLES and isinstance(value, int):
                    role_weights[role] = value

        if "role_markers" in triage_payload:
            logger.debug(
                "Ignoring profile triage.role_markers; markers are derived from profile role synonyms"
            )

    return role_weights


def configure_role_synonyms(
    input_pdf: Path,
    header_profile_file: Path | None,
    header_profile_dir: Path,
) -> None:
    """Load and activate header profile for PDF extraction.
    
    Priority:
    1) --header-profile-file (explicit)
    2) <header_profile_dir>/<pdf_stem>.json (auto)
    3) <header_profile_dir>/default.json (fallback)
    """
    profile_path: Path | None = None
    if header_profile_file is not None:
        profile_path = header_profile_file
    else:
        auto_path = header_profile_dir / f"{input_pdf.stem}.json"
        if auto_path.exists():
            profile_path = auto_path
        else:
            profile_path = header_profile_dir / "default.json"

    if not profile_path.exists():
        logger.error(
            "Header profile not found. Provide --header-profile-file or create "
            f"{header_profile_dir / (input_pdf.stem + '.json')} or {header_profile_dir / 'default.json'}."
        )
        raise SystemExit("No header profile available.")

    try:
        loaded_profile = load_profile(profile_path)
        triage_weights = load_profile_triage_config(profile_path)
        triage_markers = build_role_markers(loaded_profile)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to load header profile '{profile_path}': {exc}")
        raise SystemExit("Invalid header profile.")

    # Mutate shared dictionary in-place so all modules importing the same object
    # see updated role synonyms.
    ACTIVE_ROLE_SYNONYMS.clear()
    ACTIVE_ROLE_SYNONYMS.update(loaded_profile)

    ACTIVE_TRIAGE_ROLE_WEIGHTS.clear()
    ACTIVE_TRIAGE_ROLE_WEIGHTS.update(triage_weights)

    ACTIVE_TRIAGE_ROLE_MARKERS.clear()
    ACTIVE_TRIAGE_ROLE_MARKERS.update(triage_markers)

    logger.info(f"Loaded header profile: {profile_path}")
    logger.info(f"Profile roles applied: {', '.join(sorted(ACTIVE_ROLE_SYNONYMS.keys()))}")
    logger.info("Triage markers derived from profile role synonyms")
