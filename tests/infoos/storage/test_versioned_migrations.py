"""B1 schema and recovery checks against isolated SQLite files."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import multiprocessing
import shutil
import sqlite3
from pathlib import Path

import pytest

from backend.infrastructure.storage.database import ensure_database_initialized
from backend.infrastructure.storage.migrations.runner import (
    LATEST_VERSION,
    DatabaseNotReady,
    DatabaseVersionTooNew,
    migrate_database,
)
from backend.infrastructure.storage.migrations.v0001_baseline import SchemaDriftError
from backend.infrastructure.storage.migrations.v0002_legacy_backfills import LegacyMigrationMarkerError
from tests.infoos.storage.migration_test_helpers import drop_post_v3_schema

ROOT = Path(__file__).parents[3]
FIXTURE = Path(__file__).parent / "fixtures/b1_inventory_structure.json"
LEGACY_FIXTURE = Path(__file__).parent / "fixtures/legacy_048eeb05"
MARKERS = (
    "migration.search_index.v1", "migration.card_completeness.v2",
    "migration.x_card_enrichment.v1", "migration.task_queues.v1",
)
V0003_INDEXES = {
    "idx_tasks_created_at_task_id",
    "idx_tasks_fetch_schedule",
    "idx_run_history_scheduled_run",
    "idx_acquisition_checkpoints_run_task",
    "idx_tasks_fetch_target",
}
V0004_TABLES = {"provider_receipts", "provider_receipt_attempts"}
V0004_INDEXES = {"idx_provider_receipt_attempts_budget", "idx_provider_receipts_status_updated"}
V0005_TABLES = {"raw_item_revisions"}
V0008_TABLES = {"interest_profiles", "card_evaluations", "daily_briefs"}
V0009_INDEXES = {"idx_cards_active_raw"}
V0012_INDEXES = {"idx_cards_opportunity_raw"}
V0008_INDEXES = {"idx_card_evaluations_window", "idx_card_evaluations_card_latest"}
V0023_INDEXES = {"idx_tasks_queue_created", "idx_card_content_blocks_degraded_flags",
                 "idx_card_evaluations_passed_relevance"}


def _snapshot_structure(db: Path) -> dict[str, object]:
    with sqlite3.connect(db) as conn:
        tables = [row[0] for row in conn.execute(
            "select name from sqlite_schema where type='table' and name not like 'sqlite_%'"
        )]
        columns = {
            table: {row[1]: (row[2], row[3], row[4], row[5], row[6])
                    for row in conn.execute(f'pragma table_xinfo("{table}")')}
            for table in tables
        }
        foreign_keys = {
            table: sorted(tuple(row[2:]) for row in conn.execute(f'pragma foreign_key_list("{table}")'))
            for table in tables
        }
        indexes = {}
        for table in tables:
            for row in conn.execute(f'pragma index_list("{table}")'):
                name, unique, origin, partial = row[1:5]
                keys = tuple((item[2], item[3], item[4]) for item in
                             conn.execute(f'pragma index_xinfo("{name}")') if item[5])
                sql_row = conn.execute("select sql from sqlite_schema where name=?", (name,)).fetchone()
                sql = sql_row[0] if sql_row else None
                where = sql.lower().partition(" where ")[2].strip().rstrip(";") if sql else ""
                indexes[name] = (table, unique, origin, partial, keys, where)
        return {"columns": columns, "foreign_keys": foreign_keys, "indexes": indexes}


def _without_v0003_indexes(snapshot: dict[str, object]) -> dict[str, object]:
    indexes = dict(snapshot["indexes"])
    for name in V0003_INDEXES | V0004_INDEXES | V0008_INDEXES | V0009_INDEXES | V0012_INDEXES | V0023_INDEXES | {"idx_raw_items_first_seen_source", "idx_cards_status_sort", "idx_cards_status_published"}:
        indexes.pop(name, None)
    later_tables = V0004_TABLES | V0005_TABLES | V0008_TABLES | {"source_change_audit", "opportunity_breakdowns", "opportunity_verifications", "notification_outbox", "auth_qr_rounds", "opportunity_follow_up_events"}
    later_tables |= {"reader_page_views", "raw_item_candidate_flags"}
    later_tables |= {"knowledge_cards", "knowledge_extractions", "knowledge_evidence", "knowledge_status_events"}
    later_tables |= {name for name in snapshot['columns'] if name.startswith(('card_text_fts', 'card_block_text_fts'))}
    indexes = {name: value for name, value in indexes.items() if value[0] not in later_tables}
    columns = {name: value for name, value in snapshot["columns"].items()
               if name not in later_tables}
    columns["cards"] = {name: value for name, value in columns["cards"].items()
                        if name not in {"sort_ts", "published_ts"}}
    columns["raw_items"] = {name: value for name, value in columns["raw_items"].items()
                            if name not in {"content_hash", "revision"}}
    columns["api_tokens"] = {name: value for name, value in columns["api_tokens"].items()
                              if name != "webhook_include_backfill"}
    return {**snapshot, "indexes": indexes,
            "columns": columns,
            "foreign_keys": {name: value for name, value in snapshot["foreign_keys"].items()
                             if name not in later_tables}}


def _historical_database(path: Path, *, missing_columns: bool = False,
                         markers: bool = True) -> None:
    """Construct a data-free historical layout from the offline inventory."""
    structure = json.loads(FIXTURE.read_text())
    removed = {
        "sources": "calibration_input_version",
        "tasks": "queue_name",
        "api_tokens": "webhook_last_error",
        "run_history": "visible_count",
    } if missing_columns else {}
    with sqlite3.connect(path) as conn:
        for entry in structure["schema"]:
            if entry["type"] != "table" or entry["name"].startswith("infoos_search_"):
                continue
            sql = entry["sql"]
            if entry["name"] in removed:
                name = removed[entry["name"]]
                lines = sql.splitlines(keepends=True)
                sql = "".join(line for line in lines if not line.lstrip().startswith(name + " "))
            conn.execute(sql)
        for entry in structure["schema"]:
            if entry["type"] != "index" or not entry["sql"]:
                continue
            name = removed.get(entry["tbl_name"])
            if name and any(item["key"] and item["name"] == name
                            for item in structure["indexes"][entry["name"]]["xinfo"]):
                continue
            conn.execute(entry["sql"])
        if markers:
            conn.executemany(
                "insert into settings(key,value,updated_at) values (?,'complete',datetime('now'))",
                [(marker,) for marker in MARKERS],
            )


def _spawn_migrate(db: str, ready: multiprocessing.synchronize.Event,
                   results: multiprocessing.queues.Queue) -> None:
    try:
        ready.wait(30)
        migrate_database(Path(db))
        results.put("ok")
    except Exception as exc:
        results.put(f"{type(exc).__name__}: {exc}")


def _legacy_initializer():
    source = LEGACY_FIXTURE / "database.py.txt"
    loader = SourceFileLoader("b1_legacy_database", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader, origin=str(source))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(source)
    spec.loader.exec_module(module)
    return module


def test_fresh_database_and_repeat_start(tmp_path: Path) -> None:
    db = tmp_path / "fresh.sqlite3"
    first = migrate_database(db)
    second = migrate_database(db)
    assert (first.before_version, first.after_version) == (0, LATEST_VERSION)
    assert (second.before_version, second.after_version) == (LATEST_VERSION, LATEST_VERSION)
    assert ensure_database_initialized(db) == db.resolve()
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == LATEST_VERSION
        assert dict(conn.execute("select key,value from settings where key like 'migration.%'")) == {
            key: "complete" for key in MARKERS
        }


def test_existing_v0002_database_adds_later_versions(tmp_path: Path) -> None:
    db = tmp_path / "v2.sqlite3"
    migrate_database(db)
    with sqlite3.connect(db) as conn:
        for name in V0003_INDEXES:
            conn.execute(f'drop index "{name}"')
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=2")

    report = migrate_database(db)

    assert (report.before_version, report.after_version) == (2, LATEST_VERSION)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == LATEST_VERSION
        assert V0003_INDEXES <= {
            str(row[0]) for row in conn.execute(
                "select name from sqlite_schema where type = 'index'"
            )
        }


def test_existing_v0004_database_adds_raw_revisions_without_backfill(tmp_path: Path) -> None:
    from backend.infrastructure.storage.migrations import runner

    def interrupt(event):
        if event["migration"] == 5:
            raise RuntimeError("interrupted")

    db = tmp_path / "v4.sqlite3"
    with pytest.raises(RuntimeError, match="interrupted"):
        runner.migrate_database(db, progress_callback=interrupt)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 4
        conn.execute(
            """insert into raw_items
               (connector_id, raw_item_id, source_platform, source_type, source_url,
                captured_at, raw_payload, first_seen_at, last_seen_at)
               values ('c', 'r', 'rss', 'article', 'https://example.com',
                       '2026-01-01', '{}', '2026-01-01', '2026-01-01')"""
        )
    assert runner.migrate_database(db).after_version == LATEST_VERSION
    assert runner.migrate_database(db).before_version == LATEST_VERSION
    with sqlite3.connect(db) as conn:
        assert conn.execute("select content_hash, revision from raw_items").fetchone() == (None, 0)
        assert conn.execute("select count(*) from raw_item_revisions").fetchone()[0] == 0


@pytest.mark.parametrize("markers", [True, False])
def test_inventory_column_positions_and_missing_legacy_columns(tmp_path: Path, markers: bool) -> None:
    db = tmp_path / "history.sqlite3"
    _historical_database(db, missing_columns=True, markers=markers)
    migrate_database(db, batch_size=2)
    fresh = tmp_path / "fresh.sqlite3"
    migrate_database(fresh)
    assert _snapshot_structure(db) == _snapshot_structure(fresh)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == LATEST_VERSION


def test_full_inventory_structure_accepts_historical_column_positions(tmp_path: Path) -> None:
    db = tmp_path / "history.sqlite3"
    _historical_database(db)
    before = json.loads(FIXTURE.read_text())
    for table in ("runtime_circuits", "sources", "tasks"):
        assert list(before["table_xinfo"][table])
    migrate_database(db)
    fresh = tmp_path / "fresh.sqlite3"
    migrate_database(fresh)
    assert _snapshot_structure(db) == _snapshot_structure(fresh)
    with sqlite3.connect(db) as old, sqlite3.connect(fresh) as new:
        assert [r[1] for r in old.execute("pragma table_xinfo(sources)")] != [
            r[1] for r in new.execute("pragma table_xinfo(sources)")]


def test_nine_spawn_processes_share_one_upgrade(tmp_path: Path) -> None:
    db = tmp_path / "shared.sqlite3"
    _historical_database(db, markers=False)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    results = ctx.Queue()
    processes = [ctx.Process(target=_spawn_migrate, args=(str(db), ready, results)) for _ in range(9)]
    for process in processes:
        process.start()
    ready.set()
    outcomes = [results.get(timeout=90) for _ in processes]
    for process in processes:
        process.join(90)
    assert outcomes == ["ok"] * 9
    assert all(process.exitcode == 0 for process in processes)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == LATEST_VERSION


def test_version_too_new_and_readonly_ensure(tmp_path: Path) -> None:
    db = tmp_path / "missing.sqlite3"
    with pytest.raises(DatabaseNotReady):
        ensure_database_initialized(db)
    assert not db.exists()
    with sqlite3.connect(db) as conn:
        conn.execute(f"pragma user_version={LATEST_VERSION + 1}")
    with pytest.raises(DatabaseVersionTooNew):
        migrate_database(db)
    with pytest.raises(DatabaseVersionTooNew):
        ensure_database_initialized(db)
    with sqlite3.connect(db) as conn:
        conn.execute("pragma user_version=1")
    with pytest.raises(DatabaseNotReady):
        ensure_database_initialized(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 1
    ready_db = tmp_path / "ready.sqlite3"
    migrate_database(ready_db)
    ensure_database_initialized(ready_db)
    with sqlite3.connect(ready_db) as conn:
        conn.execute(f"pragma user_version={LATEST_VERSION + 1}")
    with pytest.raises(DatabaseVersionTooNew):
        ensure_database_initialized(ready_db)


def test_v0002_batch_interruption_resumes(tmp_path: Path) -> None:
    db = tmp_path / "interrupt.sqlite3"
    _historical_database(db, markers=False)
    with sqlite3.connect(db) as conn:
        for n in range(5):
            conn.execute("""insert into cards(card_id,source_platform,source_type,source_url,
                         title,content,captured_at,processed_at) values (?,?,?,?,?,?,?,?)""",
                         (f"card-{n}", "rss", "article", f"https://example.com/{n}",
                          f"Card {n}", "body", "2026-09-23", "2026-09-23"))
    seen = 0

    def interrupt(batch: dict[str, object]) -> None:
        nonlocal seen
        seen += 1
        if seen == 3:
            raise RuntimeError("injected interruption")

    with pytest.raises(RuntimeError, match="injected interruption"):
        migrate_database(db, batch_size=2, progress_callback=interrupt)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 1
    migrate_database(db, batch_size=2)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == LATEST_VERSION
        assert conn.execute("select count(*) from infoos_search where entity_type='card'").fetchone()[0] == 0
        assert conn.execute("select count(*) from card_text_fts").fetchone()[0] == 5
        assert dict(conn.execute("select key,value from settings where key like 'migration.%'")) == {
            key: "complete" for key in MARKERS
        }


def test_fts_deletion_is_bounded_and_resumable(tmp_path: Path) -> None:
    db = tmp_path / "fts.sqlite3"
    _historical_database(db, markers=False)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "insert into infoos_search(entity_type,entity_id,title,body,source) values ('card',?,'stale','','')",
            [(f"old-{n}",) for n in range(7)],
        )
    events: list[dict[str, object]] = []

    def interrupt(batch: dict[str, object]) -> None:
        events.append(batch)
        if batch["phase"] == "search_index_v1" and batch["rows"] == 2:
            raise RuntimeError("after bounded FTS delete")

    with pytest.raises(RuntimeError, match="bounded FTS"):
        migrate_database(db, batch_size=2, progress_callback=interrupt)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 1
        assert conn.execute("select count(*) from infoos_search").fetchone()[0] in (5, 7)
    report = migrate_database(db, batch_size=2)
    assert max(batch["rows"] for batch in report.batches) <= 2
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from infoos_search").fetchone()[0] == 0


def test_index_and_fts_drift_classification(tmp_path: Path) -> None:
    db = tmp_path / "drift.sqlite3"
    migrate_database(db)
    with sqlite3.connect(db) as conn:
        conn.execute("drop index idx_sources_scope_enabled")
        conn.execute("create index idx_sources_scope_enabled on sources(enabled)")
    report = migrate_database(db)
    assert any(batch.get("warnings") for batch in report.batches)
    with sqlite3.connect(db) as conn:
        conn.execute("drop index idx_source_cursors_connector")
        conn.execute("create index idx_source_cursors_connector on source_cursors(connector_id)")
    with pytest.raises(SchemaDriftError, match="index drift"):
        migrate_database(db)
    with sqlite3.connect(db) as conn:
        conn.execute("drop index idx_source_cursors_connector")
        conn.execute("create unique index idx_source_cursors_connector on source_cursors(connector_id)")
        conn.execute("drop table infoos_search")
        conn.execute("create virtual table infoos_search using fts5(entity_type, entity_id, title, body)")
    with pytest.raises(SchemaDriftError, match="virtual-table drift"):
        migrate_database(db)


def test_unknown_legacy_marker_value_rejects_without_advancing(tmp_path: Path) -> None:
    db = tmp_path / "bad-marker.sqlite3"
    _historical_database(db, markers=True)
    with sqlite3.connect(db) as conn:
        conn.execute("update settings set value='running' where key='migration.search_index.v1'")
    with pytest.raises(LegacyMigrationMarkerError, match="migration.search_index.v1"):
        migrate_database(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("pragma user_version").fetchone()[0] == 1


def test_frozen_sql_and_rollback_structure_match_048eeb05(tmp_path: Path) -> None:
    legacy = _legacy_initializer()
    for current, historical in (("v0001_core.sql", "schema.sql"),
                                ("v0001_collector.sql", "schema_collector.sql")):
        frozen = ROOT / "backend/infrastructure/storage/migrations" / current
        assert frozen.read_bytes() == (LEGACY_FIXTURE / historical).read_bytes()
    old_db = tmp_path / "old.sqlite3"
    legacy.initialize_database(old_db)
    new_db = tmp_path / "new.sqlite3"
    migrate_database(new_db)
    assert _snapshot_structure(old_db) == _without_v0003_indexes(_snapshot_structure(new_db))
    rollback_db = tmp_path / "rollback.sqlite3"
    shutil.copy2(new_db, rollback_db)
    legacy.initialize_database(rollback_db)
    assert _without_v0003_indexes(_snapshot_structure(rollback_db)) == _snapshot_structure(old_db)
