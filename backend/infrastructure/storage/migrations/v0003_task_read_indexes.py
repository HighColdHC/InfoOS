"""Indexes for bounded task-history read paths."""

from __future__ import annotations

import sqlite3

from .v0001_baseline import SchemaDriftError


INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_tasks_created_at_task_id",
        "create index if not exists idx_tasks_created_at_task_id "
        "on tasks(created_at desc, task_id)",
    ),
    (
        "idx_tasks_fetch_schedule",
        "create index if not exists idx_tasks_fetch_schedule on tasks("
        "coalesce(julianday(coalesce(json_extract(artifacts_summary, '$.scheduled_for'), "
        "next_run_at, started_at, created_at)), -1.0e20) desc, task_id) "
        "where task_type = 'fetch'",
    ),
    (
        "idx_run_history_scheduled_run",
        "create index if not exists idx_run_history_scheduled_run "
        "on run_history(coalesce(julianday(scheduled_for), -1.0e20) desc, run_id)",
    ),
    (
        "idx_acquisition_checkpoints_run_task",
        "create index if not exists idx_acquisition_checkpoints_run_task "
        "on acquisition_checkpoints(run_id, task_id)",
    ),
    (
        "idx_tasks_fetch_target",
        "create index if not exists idx_tasks_fetch_target "
        "on tasks(target_id) where task_type = 'fetch'",
    ),
)


def apply(conn: sqlite3.Connection) -> None:
    """Create additive indexes inside the migration runner's transaction."""
    for _name, sql in INDEXES:
        conn.execute(sql)


def validate(conn: sqlite3.Connection) -> None:
    """Reject an index with the expected name but incompatible key semantics."""
    expected_keys = {
        "idx_tasks_created_at_task_id": (("created_at", 1), ("task_id", 0)),
        "idx_tasks_fetch_schedule": (("<expression>", 1), ("task_id", 0)),
        "idx_run_history_scheduled_run": (("<expression>", 1), ("run_id", 0)),
        "idx_acquisition_checkpoints_run_task": (("run_id", 0), ("task_id", 0)),
        "idx_tasks_fetch_target": (("target_id", 0),),
    }
    for name, expected_sql in INDEXES:
        keys = tuple(
            (str(row[2]) if row[2] is not None else "<expression>", int(row[3]))
            for row in conn.execute(f'pragma index_xinfo("{name}")')
            if int(row[5]) == 1
        )
        expected = expected_keys[name]
        if keys != expected:
            raise SchemaDriftError(f"index {name} has incompatible keys: {keys!r}")
        schema_row = conn.execute(
            "select sql from sqlite_schema where type = 'index' and name = ?", (name,)
        ).fetchone()
        actual = " ".join(str(schema_row[0] if schema_row else "").lower().split())
        actual = actual.replace(" if not exists", "")
        expected_ddl = " ".join(expected_sql.lower().replace(" if not exists", "").split())
        if actual != expected_ddl:
            raise SchemaDriftError(f"index {name} has incompatible SQL: {actual!r}")
