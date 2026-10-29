from __future__ import annotations

import re
import sqlite3

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.source_run_candidate_queries import _stream_sql
from backend.infrastructure.storage.task_queries import TaskReadQuery
from tests.infoos.storage.migration_test_helpers import migrate_database_path


def test_recent_task_keyset_preserves_tied_timestamp_order(tmp_path):
    db = migrate_database_path(resolve_sqlite_path(tmp_path))
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("task-c", "task-a", "task-b", "older"):
        repo.enqueue(Task(task_id, TaskType.FETCH, "source", "source-1"))
    with sqlite3.connect(db) as conn:
        conn.execute(
            "update tasks set created_at='2026-09-28T02:00:00Z' where task_id != 'older'"
        )
        conn.execute(
            "update tasks set created_at='2026-09-28T01:00:00Z' where task_id = 'older'"
        )

    query = TaskReadQuery(tmp_path)
    expected = [task.task_id for task in query.list_tasks(limit=10)]
    first = query.list_tasks(limit=2)
    cursor = (first[-1].created_at, first[-1].task_id)
    second = query.list_tasks(limit=2, before=cursor)

    assert expected == ["task-a", "task-b", "task-c", "older"]
    assert [task.task_id for task in [*first, *second]] == expected


def test_v0003_recent_and_type_queries_use_covering_order_indexes(tmp_path):
    db = migrate_database_path(resolve_sqlite_path(tmp_path))
    with sqlite3.connect(db) as conn:
        recent = " ".join(
            str(row[3]) for row in conn.execute(
                "explain query plan select task_id from tasks "
                "order by created_at desc, task_id limit 100"
            )
        )
        typed = " ".join(
            str(row[3]) for row in conn.execute(
                "explain query plan select task_id from tasks where task_type='fetch' "
                "order by coalesce(julianday(coalesce(json_extract(artifacts_summary, "
                "'$.scheduled_for'), next_run_at, started_at, created_at)), -1.0e20) "
                "desc, task_id limit 100"
            )
        )

    assert "idx_tasks_created_at_task_id" in recent
    assert "idx_tasks_fetch_schedule" in typed
    assert "TEMP B-TREE" not in recent
    assert "TEMP B-TREE" not in typed


def test_source_run_history_streams_use_v0003_schedule_indexes(tmp_path):
    db = migrate_database_path(resolve_sqlite_path(tmp_path))
    plans = {}
    cursor_plans = {}
    with sqlite3.connect(db) as conn:
        for kind in ("committed", "fallback", "planned"):
            sql, params = _stream_sql(
                kind, now="2026-09-28T00:00:00Z", query=None, platform=None,
                time_from=None, time_to=None, execution_scope="all", task_view="all",
                cursor=None, limit=25, count=False,
            )
            plans[kind] = [
                str(row[3]) for row in conn.execute("explain query plan " + sql, params)
            ]
            if kind != "planned":
                cursor_sql, cursor_params = _stream_sql(
                    kind, now="2026-09-28T00:00:00Z", query=None, platform=None,
                    time_from=None, time_to=None, execution_scope="all", task_view="all",
                    cursor=(1, 2461000.5, "task-cursor"), limit=25, count=False,
                )
                cursor_plans[kind] = [
                    str(row[3]) for row in conn.execute(
                        "explain query plan " + cursor_sql, cursor_params
                    )
                ]

    # Indexed scans can stream history in order; unindexed scans of the large
    # history/task/checkpoint tables cannot. Ignore version-specific sort wording.
    for plan in [*plans.values(), *cursor_plans.values()]:
        for detail in plan:
            if re.match(r"SCAN (?:TABLE )?(?:r|t|c)\b", detail):
                assert re.search(r"\bUSING (?:COVERING )?INDEX\b", detail), plan
    plans = {kind: " ".join(plan) for kind, plan in plans.items()}
    cursor_plans = {kind: " ".join(plan) for kind, plan in cursor_plans.items()}
    assert "idx_run_history_scheduled_run" in plans["committed"]
    assert "idx_acquisition_checkpoints_run_task" in plans["committed"]
    assert "idx_tasks_fetch_schedule" in plans["fallback"]
    assert "TEMP B-TREE" not in plans["fallback"]
    assert "idx_tasks_fetch_target" in plans["planned"]
    assert "SEARCH r USING INDEX idx_run_history_scheduled_run (<expr><?)" in cursor_plans["committed"]
    assert "SEARCH t USING INDEX idx_tasks_fetch_schedule (<expr><?)" in cursor_plans["fallback"]
