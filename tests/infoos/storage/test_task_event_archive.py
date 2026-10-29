from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from backend.application.workflows import task_event_archive as archive_workflow
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.task_event_archive import (
    TaskEventArchiveStore,
    archive_month,
    hot_window_days,
)
from backend.workers.task_event_archive import main as archive_cli


NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def _insert_task(base_dir: Path, task_id: str, status: str) -> None:
    with sqlite3.connect(default_database_path(base_dir)) as conn:
        conn.execute(
            """
            insert into tasks (
              task_id, task_type, target_kind, target_id, status,
              depends_on, artifacts_summary, created_at, updated_at
            ) values (?, 'fetch', 'source', 'source_1', ?, '[]', '{}', ?, ?)
            """,
            (task_id, status, "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
        )


def _insert_event(
    base_dir: Path,
    event_id: str,
    occurred_at: str,
    *,
    task_id: str | None = None,
    chain_id: str | None = None,
    event_type: str = "completed",
) -> None:
    with sqlite3.connect(default_database_path(base_dir)) as conn:
        conn.execute(
            """
            insert into task_events (
              event_id, task_id, chain_id, event_type, status,
              failure_class, payload, occurred_at
            ) values (?, ?, ?, ?, 'success', null, '{"ok":true}', ?)
            """,
            (event_id, task_id, chain_id, event_type, occurred_at),
        )


def _ids(rows: list[dict]) -> set[str]:
    return {str(row["event_id"]) for row in rows}


def test_eligible_predicate_covers_windows_task_state_and_auth_recovery(tmp_path: Path) -> None:
    _insert_task(tmp_path, "terminal", "success")
    _insert_task(tmp_path, "running", "running")
    _insert_task(tmp_path, "auth", "failed")
    _insert_event(tmp_path, "old_terminal", "2026-08-01T00:00:00Z", task_id="terminal")
    _insert_event(tmp_path, "recent", "2026-09-01T00:00:00Z", task_id="terminal")
    _insert_event(tmp_path, "old_running", "2026-01-01T00:00:00Z", task_id="running")
    _insert_event(tmp_path, "critical_kept", "2026-05-01T00:00:00Z", task_id="terminal", event_type="failed")
    _insert_event(tmp_path, "critical_old", "2026-01-01T00:00:00Z", task_id="terminal", event_type="auth_recovery_started")
    _insert_event(tmp_path, "lookalike_prefix", "2026-05-01T00:00:00Z", task_id="terminal", event_type="authXrecovery_started")
    _insert_event(tmp_path, "orphan", "2026-01-01T00:00:00Z", task_id="missing")
    _insert_event(tmp_path, "no_task", "2026-01-01T00:00:00Z")
    _insert_event(tmp_path, "offset_time", "2026-08-28T01:00:00+02:00", task_id="terminal")
    _insert_event(tmp_path, "fraction_after_cutoff", "2026-08-28T12:00:00.500Z", task_id="terminal")
    _insert_event(tmp_path, "invalid_time", "not-a-time", task_id="terminal")
    _insert_event(tmp_path, "auth_unresolved", "2026-01-01T00:00:00Z", task_id="auth")
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute(
            """
            insert into auth_recovery_incidents (
              incident_id, platform, outage_epoch, reason_code, status, created_at, updated_at
            ) values ('incident_1', 'xhs', 1, 'login', 'awaiting_user', ?, ?)
            """,
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "insert into auth_recovery_incident_tasks(incident_id, task_id, created_at) values ('incident_1','auth',?)",
            ("2026-01-01T00:00:00Z",),
        )

    assert _ids(TaskEventArchiveStore(tmp_path).select_eligible(now=NOW, limit=100)) == {
        "old_terminal",
        "critical_old",
        "lookalike_prefix",
        "orphan",
        "no_task",
        "offset_time",
    }


def test_hot_window_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFOOS_TASK_EVENT_HOT_WINDOW_DAYS", "45")
    assert hot_window_days() == 45


def test_two_phase_reruns_after_archive_write_and_delete_boundary(tmp_path: Path) -> None:
    _insert_task(tmp_path, "terminal", "success")
    _insert_event(tmp_path, "jan", "2026-01-15T00:00:00Z", task_id="terminal", chain_id="chain")
    _insert_event(tmp_path, "feb", "2026-02-15T00:00:00Z", task_id="terminal", chain_id="chain")
    store = TaskEventArchiveStore(tmp_path)
    rows = store.select_eligible(now=NOW, limit=100)

    store.archive_rows(rows, archived_at="2026-09-27T12:00:00Z")
    # Simulate an interruption after archive commit, then another before delete.
    store.archive_rows(rows, archived_at="2026-09-27T12:01:00Z")
    store.verify_rows(rows)
    store.verify_rows(rows)
    deleted, _ = store.delete_verified(rows, now=NOW)
    assert deleted == 2
    assert store.delete_verified(rows, now=NOW)[0] == 0
    assert len(store.query(chain_id="chain")) == 2
    assert sorted(path.name for path in store.archive_dir.glob("*.sqlite3")) == [
        "task_events-2026-01.sqlite3",
        "task_events-2026-02.sqlite3",
    ]
    assert (store.archive_dir.stat().st_mode & 0o777) == 0o700
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in store.archive_dir.glob("*.sqlite3"))


def test_verification_failure_preserves_hot_row_and_rerun_does_not_duplicate(tmp_path: Path) -> None:
    _insert_task(tmp_path, "terminal", "failed")
    _insert_event(tmp_path, "event_1", "2026-01-15T00:00:00Z", task_id="terminal")
    store = TaskEventArchiveStore(tmp_path)
    rows = store.select_eligible(now=NOW, limit=10)
    [path] = store.archive_rows(rows, archived_at="2026-09-27T12:00:00Z")
    with sqlite3.connect(path) as conn:
        conn.execute("update task_events set payload = '{}' where event_id = 'event_1'")

    with pytest.raises(RuntimeError, match="verification failed"):
        store.verify_rows(rows)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_events").fetchone()[0] == 1

    with sqlite3.connect(path) as conn:
        conn.execute("delete from task_events where event_id = 'event_1'")
    store.archive_rows(rows, archived_at="2026-09-27T12:05:00Z")
    store.verify_rows(rows)
    assert store.delete_verified(rows, now=NOW)[0] == 1
    with sqlite3.connect(path) as conn:
        assert conn.execute("select count(*) from task_events").fetchone()[0] == 1


def test_transient_verification_failure_reruns_without_manual_repair(tmp_path: Path, monkeypatch) -> None:
    _insert_task(tmp_path, "terminal", "success")
    _insert_event(tmp_path, "event_1", "2026-01-15T00:00:00Z", task_id="terminal")
    original = TaskEventArchiveStore.verify_rows
    calls = 0

    def fail_once(self, rows, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient verification interruption")
        return original(self, rows, **kwargs)

    monkeypatch.setattr(TaskEventArchiveStore, "verify_rows", fail_once)
    with pytest.raises(RuntimeError, match="transient"):
        archive_workflow.archive_task_events(tmp_path, now=NOW)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_events").fetchone()[0] == 1
    result = archive_workflow.archive_task_events(tmp_path, now=NOW)
    assert result["deleted_rows"] == 1
    path = next(TaskEventArchiveStore(tmp_path).archive_dir.glob("*.sqlite3"))
    with sqlite3.connect(path) as conn:
        assert conn.execute("select count(*) from task_events").fetchone()[0] == 1


def test_delete_rechecks_eligibility_after_archive_commit(tmp_path: Path) -> None:
    _insert_task(tmp_path, "task", "success")
    _insert_event(tmp_path, "event", "2026-01-15T00:00:00Z", task_id="task")
    store = TaskEventArchiveStore(tmp_path)
    rows = store.select_eligible(now=NOW, limit=10)
    store.archive_rows(rows, archived_at="2026-09-27T12:00:00Z")
    store.verify_rows(rows)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update tasks set status = 'running' where task_id = 'task'")
    assert store.delete_verified(rows, now=NOW)[0] == 0
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_events").fetchone()[0] == 1


def test_workflow_obeys_row_budget_and_emits_daily_stats_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert_task(tmp_path, "terminal", "success")
    for index in range(5):
        _insert_event(tmp_path, f"event_{index}", "2026-01-15T00:00:00Z", task_id="terminal")
    monkeypatch.setattr(archive_workflow, "archive_batch_size", lambda: 2)
    monkeypatch.setattr(archive_workflow, "archive_max_rows", lambda: 3)
    monkeypatch.setattr(archive_workflow, "archive_max_seconds", lambda: 300)
    logged: list[str] = []
    monkeypatch.setattr(archive_workflow.logger, "info", lambda message, **_kwargs: logged.append(message))

    result = archive_workflow.archive_task_events(tmp_path, now=NOW)
    again = archive_workflow.archive_task_events(tmp_path, now=NOW)

    assert result["archived_rows"] == result["deleted_rows"] == 3
    assert result["remaining_eligible_estimate"] == 2
    assert result["history_stats"]["tasks_rows"] == 1
    assert result["history_stats"]["task_events_rows"] == 2
    assert {"main_db_size_bytes", "page_count", "freelist_count", "freelist_ratio", "archive_size_bytes", "tasks_added_24h", "events_added_24h"} <= result["history_stats"].keys()
    assert again["deleted_rows"] == 2
    assert len(logged) == 1
    assert json.loads(logged[0])["event"] == "task_history_stats"


def test_workflow_obeys_time_budget_between_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _insert_task(tmp_path, "terminal", "success")
    for index in range(3):
        _insert_event(tmp_path, f"event_{index}", "2026-01-15T00:00:00Z", task_id="terminal")
    clock = [0.0]
    original_archive = TaskEventArchiveStore.archive_rows

    def archive_then_expire(self, rows, **kwargs):
        paths = original_archive(self, rows, **kwargs)
        clock[0] = 1.0
        return paths

    monkeypatch.setattr(TaskEventArchiveStore, "archive_rows", archive_then_expire)
    monkeypatch.setattr(archive_workflow, "archive_batch_size", lambda: 1)
    monkeypatch.setattr(archive_workflow, "archive_max_rows", lambda: 50)
    monkeypatch.setattr(archive_workflow, "archive_max_seconds", lambda: 1)
    result = archive_workflow.archive_task_events(tmp_path, now=NOW, monotonic=lambda: clock[0])
    assert result["deleted_rows"] == 0
    assert result["elapsed_seconds"] == 1.0
    assert result["remaining_eligible_estimate"] is None
    assert result["history_stats"]["task_events_rows"] is None
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_events").fetchone()[0] == 3


def test_cli_query_merges_hot_and_archived_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _insert_task(tmp_path, "terminal", "success")
    _insert_event(tmp_path, "cold", "2026-01-15T00:00:00Z", task_id="terminal", chain_id="chain")
    store = TaskEventArchiveStore(tmp_path)
    rows = store.select_eligible(now=NOW, limit=10)
    store.archive_rows(rows, archived_at="2026-09-27T12:00:00Z")
    store.verify_rows(rows)
    store.delete_verified(rows, now=NOW)
    _insert_event(tmp_path, "hot", "2026-09-20T00:00:00Z", task_id="terminal", chain_id="chain")

    archive_cli(["query", "--base-dir", str(tmp_path), "--task-id", "terminal"])
    output = json.loads(capsys.readouterr().out)
    assert [row["event_id"] for row in output] == ["cold", "hot"]
    assert [(row["storage"], row["archived_at"] is not None) for row in output] == [
        ("archive", True),
        ("hot", False),
    ]
    assert archive_month("2026-01-31T23:00:00-02:00") == "2026-02"
