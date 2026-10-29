"""Task batch writes on a caller-owned SQLite connection."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.infrastructure.storage.tasks.operations import task_ids_present


def persist_task_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: str,
    batch_type: str,
    task_ids: list[str],
    source_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str,
) -> None:
    """Write one non-empty batch using the caller-owned transaction."""
    unique_task_ids = list(dict.fromkeys(str(task_id) for task_id in task_ids if task_id))
    if not unique_task_ids:
        raise ValueError("task batch requires at least one root task")
    found = task_ids_present(conn, unique_task_ids)
    missing = [task_id for task_id in unique_task_ids if task_id not in found]
    if missing:
        raise ValueError(f"missing task ids: {', '.join(missing)}")
    conn.execute(
        """insert into task_batches (
          batch_id, batch_type, status, source_ids, metadata, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?)""",
        (batch_id, batch_type, "queued", json.dumps(source_ids or []),
         json.dumps(metadata or {}), created_at, created_at),
    )
    _insert_batch_items(
        conn, batch_id=batch_id, task_ids=unique_task_ids, created_at=created_at,
    )


def _insert_batch_items(
    conn: sqlite3.Connection, *, batch_id: str, task_ids: list[str], created_at: str,
) -> None:
    conn.executemany(
        """insert into task_batch_items (batch_id, task_id, created_at)
        values (?, ?, ?)""",
        [(batch_id, task_id, created_at) for task_id in task_ids],
    )


def attach_task_to_parent_batches_on_connection(
    conn: sqlite3.Connection,
    *,
    parent_task_id: str,
    child_task_ids: list[str],
    created_at: str,
) -> int:
    attached = 0
    for child_task_id in child_task_ids:
        cursor = conn.execute(
            """insert or ignore into task_batch_items (batch_id, task_id, created_at)
            select batch_id, ?, ? from task_batch_items where task_id = ?""",
            (child_task_id, created_at, parent_task_id),
        )
        attached += cursor.rowcount
    return attached
