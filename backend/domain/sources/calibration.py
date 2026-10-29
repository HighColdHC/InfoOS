"""Compatibility name for the canonical collection input fingerprint."""
from __future__ import annotations

import json
from typing import Any

from backend.domain.sources.versioning import source_fetch_input_version


def source_calibration_version(source: dict[str, Any]) -> str:
    """Return the sole durable FETCH version under the historical function name."""
    return source_fetch_input_version(source)


def normalize_source_raw_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}
