"""Initialize the SQLite workbench schema.

The workbench is the single source of truth. This module only ensures
the schema exists and is responsible for nothing else — no legacy
YAML/JSON imports, no seed functions. Data entry happens through
the web UI or CLI sources command.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized
from backend.infrastructure.storage.migrations.runner import migrate_database


def initialize_runtime_storage(base_dir: Path | str | None = None) -> Path:
    """Initialize the canonical runtime SQLite database and return its path."""
    return migrate_database(default_database_path(base_dir)).path


def ensure_workbench_seeded(base_dir: Path | str) -> dict[str, Any]:
    """Assert startup migration completed. Returns legacy import counts (always 0)."""
    ensure_database_initialized(default_database_path(base_dir))
    return {"sources": 0, "cards": 0, "topics": 0}
