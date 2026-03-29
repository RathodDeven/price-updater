"""Configuration and profile loading."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.models import (
    ACTIVE_ROLE_SYNONYMS,
    ACTIVE_TRIAGE_ROLE_MARKERS,
    ACTIVE_TRIAGE_ROLE_WEIGHTS,
    REQUIRED_PROFILE_ROLES,
)

logger = logging.getLogger(__name__)


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


def load_profile_triage_config(
    path: Path,
    role_synonyms: dict[str, list[str]],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Load optional triage config from profile.

    Supported optional profile block:
    {
      "triage": {
        "role_weights": {"alias": 2, "purchase": 3, "pack": 2, "particulars": 2},
        "role_markers": {"alias": [...], "purchase": [...], "pack": [...], "particulars": [...]}
      }
    }
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    triage_payload = payload.get("triage", {}) if isinstance(payload, dict) else {}

    # Defaults: role-based weights + markers from loaded role synonyms.
    role_weights: dict[str, int] = {
        "alias": 2,
        "purchase": 3,
        "pack": 2,
        "particulars": 2,
    }
    role_markers: dict[str, list[str]] = {
        role: [" ".join(s.lower().split()).strip() for s in synonyms if str(s).strip()]
        for role, synonyms in role_synonyms.items()
    }

    if isinstance(triage_payload, dict):
        weights_payload = triage_payload.get("role_weights")
        if isinstance(weights_payload, dict):
            for role, value in weights_payload.items():
                if role in REQUIRED_PROFILE_ROLES and isinstance(value, int):
                    role_weights[role] = value

        markers_payload = triage_payload.get("role_markers")
        if isinstance(markers_payload, dict):
            for role in REQUIRED_PROFILE_ROLES:
                markers = markers_payload.get(role)
                if isinstance(markers, list):
                    cleaned = [" ".join(str(m).lower().split()).strip() for m in markers if str(m).strip()]
                    if cleaned:
                        role_markers[role] = cleaned

    return role_weights, role_markers


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
        triage_weights, triage_markers = load_profile_triage_config(profile_path, loaded_profile)
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
    logger.info(f"Triage role markers applied: {', '.join(sorted(ACTIVE_TRIAGE_ROLE_MARKERS.keys()))}")
