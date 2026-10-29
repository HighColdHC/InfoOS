"""Source configuration loading and shared CLI constants.

Migrated from infoos.cli._shared (PR 2a of the backend restructure; the
infoos.cli/ shim was removed in PR 6).

Re-exports the legacy constants (DEFAULT_*, TASK_TYPE_*, SOURCE_METADATA_KEYS)
so callers that haven't migrated yet still work. The function `load_source_configs`
lives here as the canonical home.
"""
from __future__ import annotations

import os
from pathlib import Path

from backend.infrastructure.browser.chrome_runtime import default_stable_chrome_binary
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.domain.task_labels import TASK_TYPE_LABELS as TASK_TYPE_LABELS, TASK_TYPE_ORDER as TASK_TYPE_ORDER

# Re-exported constants (originally in infoos.cli._shared; the
# infoos.cli/ shim was removed in PR 6).
# These will move to app/core/ in a later PR; for now they live here.
DEFAULT_INFOOS_BROWSER_LAUNCHER: Path = Path.home() / ".local" / "bin" / "opencli-infoos-chrome"
DEFAULT_INFOOS_CHROME_PROFILE_DIR: Path = chrome_profile_dir()
DEFAULT_INFOOS_CHROME_BIN: Path = default_stable_chrome_binary()
DEFAULT_INFOOS_CONFIG_PATH: Path = Path.home() / ".infoos" / "config.yaml"
DEFAULT_OPENCLI_EXTENSION_DIR: Path = Path.home() / "Downloads" / "opencli-extension" / "unpacked"

SOURCE_METADATA_KEYS = (
    "source_id",
    "source_lane",
    "discovery_reason",
    "viewpoint_hint",
    "source_confidence",
    "topics",
    "purpose",
    "review_policy",
    "feed_provider",
    "feed_content_mode",
)


def default_infoos_browser_launcher() -> Path:
    raw = os.environ.get("INFOOS_BROWSER_LAUNCHER", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_INFOOS_BROWSER_LAUNCHER


def load_source_configs(base_dir: Path) -> list[dict]:
    """Return enabled source records from SQLite, translated into
    connector-config dicts that ``_build_connector`` consumes.

    SQLite is the single source of truth.
    """
    from backend.domain.sources.configuration import database_source_to_connector_config
    from backend.infrastructure.storage.repositories import SourceRepository

    rows = SourceRepository(base_dir).list_sources()
    return [database_source_to_connector_config(row) for row in rows]
