"""Resolve persisted platform facts for the pure durable queue policy."""
from __future__ import annotations

import sqlite3

from backend.domain.task_queue_policy import QueuePolicy, queue_policy_for as pure_queue_policy
from backend.domain.tasks import Task, TaskType


def queue_policy_for(conn: sqlite3.Connection, task: Task, *, enqueued_at: str) -> QueuePolicy:
    return pure_queue_policy(
        task.task_type,
        platform=_platform_for_task(conn, task),
        enqueued_at=enqueued_at,
        next_run_at=task.next_run_at,
    )


def _platform_for_task(conn: sqlite3.Connection, task: Task) -> str:
    queue_platform = str(task.artifacts_summary.get("queue_platform") or "").strip().lower()
    if queue_platform:
        return queue_platform
    if task.task_type in {TaskType.SOURCE_CALIBRATE, TaskType.FETCH}:
        return _platform(conn, "sources", "platform", "source_id", task.target_id)
    if task.task_type in {TaskType.CONTENT_ENRICH, TaskType.MEDIA_DOWNLOAD}:
        return _platform(
            conn, "cards", "source_platform", "card_id", task.target_id
        )
    return ""


def _platform(
    conn: sqlite3.Connection, table: str, column: str, id_column: str, identifier: str
) -> str:
    row = conn.execute(
        f"select {column} from {table} where {id_column} = ?", (identifier,)
    ).fetchone()
    return str(row[column] if row else "").strip().lower()
