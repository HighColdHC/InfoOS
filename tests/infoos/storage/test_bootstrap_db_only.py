"""Bootstrap is now schema-only — no legacy YAML/JSON seeding."""
import importlib.util
from pathlib import Path
import sqlite3
import threading
from types import ModuleType, SimpleNamespace

import pytest

from tests.infoos._factories import card_count
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded, initialize_runtime_storage
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository, TopicRepository
from tests.infoos.storage.migration_test_helpers import drop_post_v3_schema


pytestmark = pytest.mark.no_storage_bootstrap


@pytest.mark.no_storage_bootstrap
def test_storage_object_construction_does_not_initialize_absent_database(tmp_path: Path) -> None:
    from backend.application.tasks.repository import TaskRepository
    from backend.infrastructure.storage.base import InfoOSRepository
    from backend.infrastructure.storage.fetch_rate_limits import FetchRateLimitRepository
    from backend.infrastructure.storage.task_queries import TaskReadQuery

    db_path = tmp_path / "data" / "infoos.sqlite3"
    InfoOSRepository(tmp_path)
    TaskRepository.for_base_dir(tmp_path)
    TaskReadQuery(tmp_path)
    FetchRateLimitRepository(tmp_path)

    assert not db_path.exists()


def test_passive_reads_do_not_create_absent_database(tmp_path: Path) -> None:
    from backend.infrastructure.observability.reliability_query import read_reliability_status
    from backend.infrastructure.storage.task_queries import TaskReadQuery

    readers = [
        lambda: CardRepository(tmp_path).get_card("absent-card"),
        lambda: TopicRepository(tmp_path).list_topics(),
        lambda: SourceRepository(tmp_path).list_sources(),
        lambda: TaskReadQuery(tmp_path).list_tasks(),
        lambda: read_reliability_status(tmp_path),
    ]
    for read in readers:
        with pytest.raises(sqlite3.OperationalError):
            read()
    assert not (tmp_path / "data").exists()


@pytest.mark.no_storage_bootstrap
def test_explicit_bootstrap_allows_normal_storage_construction(tmp_path: Path) -> None:
    from backend.application.tasks.repository import TaskRepository
    from backend.infrastructure.storage.task_batch_queries import list_task_batches
    from backend.infrastructure.storage.task_queries import TaskReadQuery

    db_path = initialize_runtime_storage(tmp_path)
    repository = TaskRepository.for_base_dir(tmp_path)
    query = TaskReadQuery(tmp_path)

    assert db_path.exists()
    assert repository.list_tasks() == []
    assert query.list_tasks() == []
    assert list_task_batches(tmp_path) == []


def test_initialize_runtime_storage_creates_schema_in_empty_database(tmp_path: Path) -> None:
    db_path = initialize_runtime_storage(tmp_path)

    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("select name from sqlite_master where type = 'table'")}
    assert {"sources", "cards", "tasks"} <= tables


def test_runtime_storage_dot_uses_configured_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_home = tmp_path / "installed-data"
    monkeypatch.setenv("INFOOS_DATA_HOME", str(data_home))

    assert initialize_runtime_storage(".") == data_home / "infoos.sqlite3"


def test_runtime_storage_explicit_base_uses_base_data_directory(tmp_path: Path) -> None:
    assert initialize_runtime_storage(tmp_path) == tmp_path / "data" / "infoos.sqlite3"


def test_runtime_storage_applies_existing_migration_and_backfill(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "infoos.sqlite3"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "create table tasks (task_id text primary key, task_type text not null, "
            "target_kind text not null, target_id text not null, status text not null, "
            "depends_on text not null default '[]', attempt integer not null default 1, "
            "max_attempts integer not null default 3, next_run_at text, error_reason text, "
            "artifacts_summary text not null default '{}', created_at text not null, updated_at text not null)"
        )

    initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        task_columns = {row[1] for row in conn.execute("pragma table_info(tasks)")}
        conn.execute(
            "insert into sources (source_id, platform, source_type, identifier, display_name, "
            "cadence_hours, connector_status, raw_metadata, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "rss", "feed", "https://example.com/feed", "Legacy", 24,
             "implemented_uncalibrated", "{}", "2026-07-11T08:00:00Z", "2026-07-11T08:00:00Z"),
        )
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=1")
    initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        calibration = conn.execute(
            "select calibration_input_version from sources where source_id = 'legacy'"
        ).fetchone()[0]
    assert {"claimed_by", "lease_expires_at", "completion_token"} <= task_columns
    assert calibration.startswith("sha256:")


@pytest.mark.parametrize("legacy_search", ["empty", "stale"])
def test_initialize_runtime_storage_repairs_search_once(tmp_path: Path, legacy_search: str) -> None:
    db_path = initialize_runtime_storage(tmp_path)
    CardRepository(tmp_path).upsert_card({
        "card_id": "card_legacy", "source_platform": "rss", "source_type": "article",
        "source_url": "https://example.com/card", "title": "Current card", "content": "current body",
        "captured_at": "2026-07-13T00:00:00Z", "processed_at": "2026-07-13T00:00:00Z",
    })
    TopicRepository(tmp_path).upsert_topic({"topic_id": "topic_legacy", "title": "Current topic"})
    with sqlite3.connect(db_path) as conn:
        conn.execute("delete from settings where key = 'migration.search_index.v1'")
        conn.execute("delete from infoos_search")
        drop_post_v3_schema(conn)
        conn.execute("pragma user_version=1")
        if legacy_search == "stale":
            conn.execute("insert into infoos_search values ('card', 'card_legacy', 'Stale', 'stale', '')")

    initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("select entity_id from infoos_search where infoos_search match 'Current'").fetchall() == [
            ('topic_legacy',)
        ]
        assert conn.execute("select card_id from cards where rowid in "
                            "(select rowid from card_text_fts where card_text_fts match 'Current')").fetchall() == [
            ('card_legacy',)
        ]

    with sqlite3.connect(db_path) as conn:
        conn.execute("delete from infoos_search")
        conn.execute("insert into infoos_search values ('card', 'sentinel', 'Sentinel', '', '')")
    initialize_runtime_storage(tmp_path)
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("select entity_id from infoos_search").fetchall()
    assert rows == [("sentinel",)]


def _load_script(name: str) -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"bootstrap_test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("entrypoint", ["workflow", "migration", "reliability"])
def test_representative_cli_bootstraps_before_storage_access(
    entrypoint: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend.infrastructure.storage import bootstrap, database

    events: list[str] = []
    monkeypatch.setattr(bootstrap, "initialize_runtime_storage", lambda _base: events.append("bootstrap"))
    monkeypatch.setattr(database, "ensure_database_initialized", lambda _path: events.append("ready"))
    if entrypoint == "workflow":
        from backend.application.workflows import source_batch
        monkeypatch.setattr(source_batch, "enqueue_source_batch", lambda *_args, **_kwargs: events.append("storage") or {})
        assert source_batch.main(["--base-dir", str(tmp_path)]) == 0
    elif entrypoint == "migration":
        module = _load_script("migrate-infoos-feedback.py")
        monkeypatch.setattr(module, "migrate_legacy_feedback", lambda _base: events.append("storage") or {})
        assert module.main(["--base-dir", str(tmp_path)]) == 0
    elif entrypoint == "reliability":
        module = _load_script("infoos-reliability-report.py")
        args = SimpleNamespace(base_dir=tmp_path, output_json=tmp_path / "report.json",
                               output_markdown=tmp_path / "report.md", public_min_rate=.98,
                               browser_min_rate=.9, max_expired_leases=0, max_running_lease_periods=2)
        monkeypatch.setattr(module, "parse_args", lambda _argv: args)
        monkeypatch.setattr(module, "read_reliability_status", lambda _base: events.append("storage") or {})
        monkeypatch.setattr(module, "build_report", lambda *_args, **_kwargs: {"status": "passed"})
        monkeypatch.setattr(module, "_markdown", lambda _report: "ok")
        assert module.main([]) == 0

    assert events[:2] == (["ready", "storage"] if entrypoint == "reliability"
                          else ["bootstrap", "storage"])


def test_web_factory_bootstraps_before_app_construction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app import main as app_main

    calls: list[Path] = []
    monkeypatch.setattr(app_main, "initialize_runtime_storage", lambda base: calls.append(Path(base)))

    app = app_main.create_app(tmp_path)

    assert calls == [tmp_path]
    assert app.state.base_dir == tmp_path


def test_worker_loop_bootstraps_before_repository_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.workers import task_worker

    events: list[str] = []
    stop = threading.Event()
    monkeypatch.setattr(task_worker, "initialize_runtime_storage", lambda _base: events.append("bootstrap"))

    def run_due(*_args, **_kwargs):
        events.append("repository")
        stop.set()
        return {"processed": 0}

    task_worker.run_task_worker_loop(base_dir=tmp_path, stop_event=stop, run_due_tasks_fn=run_due)
    assert events == ["bootstrap", "repository"]


def test_scheduler_cycle_bootstraps_before_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.workers.scheduler import cycle

    events: list[str] = []
    monkeypatch.setattr(cycle, "ensure_database_initialized", lambda _path: events.append("ready"))
    monkeypatch.setattr(cycle, "enqueue_fetch_tasks_for_enabled_sources", lambda *_args, **_kwargs: events.append("repository") or [])
    monkeypatch.setattr(cycle, "_queue_counts", lambda *_args, **_kwargs: {"pending": 0})

    result = cycle.run_scheduler_cycle(base_dir=tmp_path)
    assert events[:2] == ["ready", "repository"]
    assert result["scheduled"] == {"collector_only": True}


def test_audit_repair_preview_requires_ready_database_without_creating_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    from backend.infrastructure.storage.migrations.runner import DatabaseNotReady
    from backend.workers import audit_repair

    monkeypatch.setattr(sys, "argv", ["audit_repair", "--base-dir", str(tmp_path)])
    with pytest.raises(DatabaseNotReady):
        audit_repair.main()
    assert not (tmp_path / "data" / "infoos.sqlite3").exists()


def test_ensure_workbench_seeded_does_not_touch_legacy_yaml(tmp_path: Path) -> None:
    """A leftover source-registry.yaml must NOT be imported by bootstrap."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "source-registry.yaml").write_text(
        "sources:\n  - source_id: legacy-imported\n    platform: x\n    identity: '@legacy'\n",
        encoding="utf-8",
    )

    initialize_runtime_storage(tmp_path)
    stats = ensure_workbench_seeded(tmp_path)

    assert stats["sources"] == 0
    assert SourceRepository(tmp_path).list_sources() == []


def test_ensure_workbench_seeded_does_not_touch_legacy_cards(tmp_path: Path) -> None:
    """A leftover data/cards/*.json must NOT be imported by bootstrap."""
    cards_dir = tmp_path / "data" / "cards"
    cards_dir.mkdir(parents=True)
    (cards_dir / "legacy-card.json").write_text(
        '{"card_id": "legacy-card", "source_platform": "x", "source_url": "", "summary": "x", "captured_at": "2026-01-01T00:00:00Z", "processed_at": "2026-01-01T00:00:00Z", "normalized_content": "x"}',
        encoding="utf-8",
    )

    initialize_runtime_storage(tmp_path)
    stats = ensure_workbench_seeded(tmp_path)

    assert stats["cards"] == 0
    assert card_count(tmp_path) == 0


def test_ensure_workbench_seeded_does_not_touch_legacy_topics(tmp_path: Path) -> None:
    """A leftover topic-processing.json must NOT be imported by bootstrap."""
    topics_path = tmp_path / "data" / "topics" / "topic-processing.json"
    topics_path.parent.mkdir(parents=True)
    topics_path.write_text(
        '{"topics": [{"topic_id": "legacy-topic", "title": "Legacy"}]}',
        encoding="utf-8",
    )

    initialize_runtime_storage(tmp_path)
    stats = ensure_workbench_seeded(tmp_path)

    assert stats["topics"] == 0
    assert TopicRepository(tmp_path).list_topics() == []
