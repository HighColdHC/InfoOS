"""Task batch actions with storage-owned SQL."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.infrastructure.storage.database import connect, ensure_database_initialized
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import utc_now
from backend.infrastructure.storage.task_batch_queries import (
    get_task_batch as query_task_batch,
    list_task_batches as query_task_batches,
)
from backend.infrastructure.storage.tasks.batches import (
    persist_task_batch,
    attach_task_to_parent_batches_on_connection as _attach_task_to_parent_batches,
)


def create_task_batch(
    base_dir: Path | str,
    *,
    batch_type: str,
    task_ids: list[str],
    source_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    base_path = Path(base_dir)
    db_path = resolve_sqlite_path(base_path)
    ensure_database_initialized(db_path)
    now = created_at or utc_now()
    batch_id = f"{batch_type}_{uuid4().hex[:12]}"
    with connect(db_path) as conn:
        conn.execute("begin immediate")
        persist_task_batch(
            conn, batch_id=batch_id, batch_type=batch_type, task_ids=task_ids,
            source_ids=source_ids, metadata=metadata, created_at=now,
        )
    return get_task_batch(base_path, batch_id)


def attach_task_to_parent_batches(
    base_dir: Path | str,
    *,
    parent_task_id: str,
    child_task_ids: list[str],
    created_at: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Attach children in the caller's transaction when one is supplied."""
    child_ids = list(dict.fromkeys(str(task_id) for task_id in child_task_ids if task_id))
    if not child_ids:
        return 0
    if conn is not None:
        return _attach_task_to_parent_batches(
            conn, parent_task_id=parent_task_id, child_task_ids=child_ids,
            created_at=created_at or utc_now(),
        )
    base_path = Path(base_dir)
    db_path = resolve_sqlite_path(base_path)
    ensure_database_initialized(db_path)
    with connect(db_path) as owned_conn:
        return _attach_task_to_parent_batches(
            owned_conn, parent_task_id=parent_task_id, child_task_ids=child_ids,
            created_at=created_at or utc_now(),
        )


def get_task_batch(base_dir: Path | str, batch_id: str) -> dict[str, Any]:
    return query_task_batch(base_dir, batch_id)


def list_task_batches(base_dir: Path | str, *, limit: int = 100) -> list[dict[str, Any]]:
    return query_task_batches(base_dir, limit=limit)
