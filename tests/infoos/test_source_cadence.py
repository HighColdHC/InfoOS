"""Read-only source cadence projection and migration checks."""

from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.migrations.runner import migrate_database
from backend.infrastructure.storage.migrations.runner import LATEST_VERSION
from backend.infrastructure.storage.repositories import SourceRepository
from backend.read_models.source_cadence import _suggest
from tests.infoos.storage.migration_test_helpers import drop_v8_schema


def _source(source_id: str, platform: str = "rss") -> dict:
    return {
        "source_id": source_id, "platform": platform, "source_type": "feed",
        "identifier": f"https://example.test/{source_id}", "display_name": source_id,
        "cadence_hours": 24, "enabled": True, "connector_status": "supported",
        "connector_id": f"connector-{source_id}", "raw_metadata": {},
    }


def test_suggestion_bounds_and_output(monkeypatch):
    assert _suggest(_source("empty"), 0)["suggested_cadence_hours"] == 24
    assert _suggest(_source("slow"), 1)["suggested_cadence_hours"] == 24
    assert _suggest(_source("fast"), 700)["suggested_cadence_hours"] == 0.5
    assert _suggest(_source("browser", "xiaohongshu"), 700)["suggested_cadence_hours"] == 5
    limited = _source("limited")
    limited["raw_metadata"] = {"fetch_rate_policy": {"min_gap_seconds": 6 * 3600}}
    assert _suggest(limited, 700)["suggested_cadence_hours"] == 24
    daily_limited = _source("daily-limited")
    daily_limited["raw_metadata"] = {"fetch_rate_policy": {"daily_budget": 1}}
    assert _suggest(daily_limited, 700)["suggested_cadence_hours"] == 24
    monkeypatch.setenv("INFOOS_X_FETCH_MIN_GAP_SECONDS", str(8 * 3600))
    assert _suggest(_source("limited-x", "x"), 700)["suggested_cadence_hours"] == 24


def test_api_counts_only_recent_non_backfill_items_and_exposes_suggestion(tmp_path):
    SourceRepository(tmp_path).upsert_source(_source("feed"))
    db = default_database_path(tmp_path)
    now = datetime.now(timezone.utc)
    seen = (now - timedelta(hours=1)).isoformat(timespec="seconds").replace("+00:00", "Z")
    recent = (now - timedelta(hours=2)).isoformat(timespec="seconds").replace("+00:00", "Z")
    old = (now - timedelta(days=5)).isoformat(timespec="seconds").replace("+00:00", "Z")
    initial_seen = (now - timedelta(hours=4)).isoformat(timespec="seconds").replace("+00:00", "Z")
    calibrated = (now - timedelta(hours=3)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with sqlite3.connect(db) as conn:
        for item_id, published, first_seen in (
            ("fresh", recent, seen), ("backfill", old, seen), ("initial", initial_seen, initial_seen),
        ):
            conn.execute(
                """insert into raw_items
                   (connector_id, raw_item_id, source_id, source_platform, source_type,
                    source_url, title, captured_at, published_at, first_seen_at, last_seen_at)
                   values (?, ?, 'feed', 'rss', 'feed', 'https://example.test', '', ?, ?, ?, ?)""",
                ("connector-feed", item_id, first_seen, published, first_seen, first_seen),
            )
        conn.execute(
            """insert into source_calibrations
               (calibration_id, source_id, platform, source_type, identifier, result, task_id, calibrated_at)
               values ('cal-1', 'feed', 'rss', 'feed', 'https://example.test/feed', 'success', 'task-1', ?)""",
            (calibrated,),
        )
    client = TestClient(create_app(tmp_path))
    page = client.get("/api/configurable-sources").json()
    suggestion = page["cadence_suggestions"]["feed"]
    assert suggestion["new_items_7d"] == 1
    assert suggestion["suggested_cadence_hours"] == 24
    assert client.get("/api/configurable-sources/feed").json()["cadence_suggestion"] == suggestion


def test_cadence_index_migrates_v6_and_restart_is_idempotent(tmp_path):
    db = default_database_path(tmp_path)
    migrate_database(db)
    with sqlite3.connect(db) as conn:
        conn.execute("drop index idx_raw_items_first_seen_source")
        drop_v8_schema(conn)
        conn.execute("pragma user_version = 6")
    first = migrate_database(db)
    second = migrate_database(db)
    assert (first.before_version, first.after_version) == (6, LATEST_VERSION)
    assert (second.before_version, second.after_version) == (LATEST_VERSION, LATEST_VERSION)
    with sqlite3.connect(db) as conn:
        assert conn.execute("select name from sqlite_master where name = 'idx_raw_items_first_seen_source'").fetchone()
