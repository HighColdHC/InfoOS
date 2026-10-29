"""Tests for SQLite source updates and source-list filters.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.sources.configuration import (  # noqa: E402
    validate_source_payload,
)
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.infrastructure.storage.repositories import SourceRepository  # noqa: E402
from tests.infoos._factories import make_source_payload  # noqa: E402


# ---------------------------------------------------------------------------
# SourceRepository: include_disabled filter
# ---------------------------------------------------------------------------


def test_list_sources_includes_disabled_by_default(tmp_path):
    repo = SourceRepository(tmp_path)
    repo.upsert_source(make_source_payload(source_id="rss_on", enabled=True))
    repo.upsert_source(make_source_payload(source_id="rss_off", enabled=False))

    rows = repo.list_sources()
    ids = {r["source_id"] for r in rows}
    assert ids == {"rss_on", "rss_off"}


def test_list_sources_can_exclude_disabled(tmp_path):
    repo = SourceRepository(tmp_path)
    repo.upsert_source(make_source_payload(source_id="rss_on", enabled=True))
    repo.upsert_source(make_source_payload(source_id="rss_off", enabled=False))

    rows = repo.list_sources(include_disabled=False)
    assert [r["source_id"] for r in rows] == ["rss_on"]


# ---------------------------------------------------------------------------
# SourceRepository.update_source
# ---------------------------------------------------------------------------


def test_update_source_cadence_writes_to_sqlite(tmp_path):
    repo = SourceRepository(tmp_path)
    repo.upsert_source(make_source_payload(source_id="rss_ai", cadence_hours=24.0))

    updated = repo.update_source("rss_ai", cadence_hours=5.0)

    assert updated is not None
    assert updated["cadence_hours"] == 5.0
    # And the change is persisted (not just a stale Python object).
    fresh = repo.get_source("rss_ai")
    assert fresh["cadence_hours"] == 5.0


def test_update_source_returns_none_for_missing_source(tmp_path):
    repo = SourceRepository(tmp_path)
    assert repo.update_source("does_not_exist", cadence_hours=1.0) is None


def test_update_source_with_no_fields_is_a_noop(tmp_path):
    repo = SourceRepository(tmp_path)
    repo.upsert_source(make_source_payload(source_id="rss_ai", cadence_hours=24.0))
    result = repo.update_source("rss_ai")
    assert result is not None
    assert result["cadence_hours"] == 24.0
