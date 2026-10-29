"""Base repository class and helper utilities for storage modules.

base — module migrated to infoos in PR 4.7.
The corresponding infoos/storage/base.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

import json
import re
from pathlib import Path
from typing import Any

from backend.infrastructure.storage.database import connect, connect_existing, default_database_path


def utc_now() -> str:
    return utc_now_seconds_z()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def card_quality_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    nested = raw_payload.get("raw_payload")
    return nested if isinstance(nested, dict) else raw_payload


def sort_timestamp(value: str | None) -> str:
    from backend.domain.timestamps import normalize_timestamp
    return normalize_timestamp(value) or ""


def clean_export_timestamp(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", value)[:32] or "unknown"


class InfoOSRepository:
    def __init__(self, base_dir: Path | str, db_path: Path | str | None = None):
        self.base_dir = Path(base_dir)
        self.db_path = (Path(db_path) if db_path else default_database_path(self.base_dir)).resolve()

    def connect(self):
        return connect(self.db_path)

    def connect_existing(self):
        return connect_existing(self.db_path)
