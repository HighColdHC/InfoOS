"""Storage paths — separate program dir from data dir.

Program lives at ``~/InfoOS/`` (overwritten on every reinstall).
Data lives at ``~/Library/Application Support/InfoOS/`` (preserved across
reinstalls).

Both paths are overridable for tests and dev workflows:
  - ``INFOOS_HOME`` env var overrides program_dir
  - ``INFOOS_DATA_HOME`` env var overrides data_dir
  - ``set_data_dir_override(Path)`` overrides data_dir in-process (tests only)
"""
from __future__ import annotations

import os
from pathlib import Path
from threading import Lock

_DATA_DIR_OVERRIDE: Path | None = None
_DATA_DIR_OVERRIDE_LOCK = Lock()


def _default_data_dir() -> Path:
    env = os.environ.get("INFOOS_DATA_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Library" / "Application Support" / "InfoOS"


def data_dir() -> Path:
    """Resolve the user-data directory."""
    with _DATA_DIR_OVERRIDE_LOCK:
        if _DATA_DIR_OVERRIDE is not None:
            return _DATA_DIR_OVERRIDE
    return _default_data_dir()


def set_data_dir_override(path: Path | str) -> None:
    """Pin data_dir() to a specific path. Test-only."""
    global _DATA_DIR_OVERRIDE
    with _DATA_DIR_OVERRIDE_LOCK:
        _DATA_DIR_OVERRIDE = Path(path).expanduser()


def reset_data_dir_override() -> None:
    """Clear any in-process override. Test-only."""
    global _DATA_DIR_OVERRIDE
    with _DATA_DIR_OVERRIDE_LOCK:
        _DATA_DIR_OVERRIDE = None


def program_dir() -> Path:
    """Resolve the program directory (the install root)."""
    env = os.environ.get("INFOOS_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / "InfoOS"


def resolve_sqlite_path(base_dir: Path | str | None = None) -> Path:
    """Return the SQLite db path with test-friendly precedence.

    Resolution order:
      1. If ``base_dir`` is explicitly set (and not the default ``'.'``),
         honor it. This is how tests use ``tmp_path`` to get isolated DBs.
      2. Otherwise, fall back to the program-isolated data directory
         (~/Library/Application Support/InfoOS/infoos.sqlite3 by default).
    """
    if base_dir is not None and Path(base_dir) != Path("."):
        return Path(base_dir) / "data" / "infoos.sqlite3"
    return data_dir() / "infoos.sqlite3"


def resolve_data_dir(base_dir: Path | str | None = None) -> Path:
    """Return the runtime data directory for SQLite-adjacent artifacts."""
    if base_dir is not None and Path(base_dir) != Path("."):
        return Path(base_dir) / "data"
    return data_dir()
