"""Compatibility migrations for task ownership, leases, and completion identity."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.infoos.storage.migration_test_helpers import migrate_database_path


pytestmark = pytest.mark.no_storage_bootstrap


_LEGACY_TASK_COLUMNS = {
    "task_id",
    "task_type",
    "target_kind",
    "target_id",
    "status",
    "depends_on",
    "attempt",
    "max_attempts",
    "next_run_at",
    "error_reason",
    "artifacts_summary",
    "created_at",
    "updated_at",
}

_TASK_LEASE_COLUMNS = {
    "claimed_by",
    "lease_expires_at",
    "heartbeat_at",
    "started_at",
    "finished_at",
    "input_version",
    "result_version",
    "last_failure_class",
    "completion_token",
}
_TASK_QUEUE_COLUMNS = {"queue_name", "priority", "deadline_at"}


def _create_legacy_tasks_table(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            create table tasks (
                task_id text primary key,
                task_type text not null,
                target_kind text not null,
                target_id text not null,
                status text not null,
                depends_on text not null default '[]',
                attempt integer not null default 1,
                max_attempts integer not null default 3,
                next_run_at text,
                error_reason text,
                artifacts_summary text not null default '{}',
                created_at text not null,
                updated_at text not null
            );
            """
        )


def _task_column_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("pragma table_info(tasks)").fetchall()
    return {row[1] for row in rows}


def test_existing_tasks_table_gains_lease_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-tasks.sqlite3"
    _create_legacy_tasks_table(db_path)

    migrate_database_path(db_path)

    assert _LEGACY_TASK_COLUMNS | _TASK_LEASE_COLUMNS | _TASK_QUEUE_COLUMNS <= _task_column_names(db_path)


def test_initialize_database_is_idempotent_after_task_lease_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-tasks.sqlite3"
    _create_legacy_tasks_table(db_path)

    migrate_database_path(db_path)
    migrate_database_path(db_path)

    assert _LEGACY_TASK_COLUMNS | _TASK_LEASE_COLUMNS | _TASK_QUEUE_COLUMNS <= _task_column_names(db_path)


def test_existing_task_rows_are_backfilled_once_with_a_durable_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-tasks.sqlite3"
    _create_legacy_tasks_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into tasks (
              task_id, task_type, target_kind, target_id, status,
              created_at, updated_at
            ) values ('old-fetch', 'fetch', 'source', 'missing', 'pending',
                      '2026-07-27T00:00:00Z', '2026-07-27T00:00:00Z')
            """
        )

    migrate_database_path(db_path)
    migrate_database_path(db_path)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select queue_name, priority, deadline_at from tasks where task_id = 'old-fetch'"
        ).fetchone()
    assert row == ("public_fetch", 30, "2026-07-27T01:00:00Z")
