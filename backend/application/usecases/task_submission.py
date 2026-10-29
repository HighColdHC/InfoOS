"""Task-batch submission without process-local execution."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.application.tasks.batches import persist_task_batch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task
from backend.infrastructure.storage.database import connect, ensure_database_initialized
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import utc_now
from backend.infrastructure.storage.tasks import operations as task_storage


def submit_task_batch(
    base_dir: Path | str,
    *,
    batch_type: str,
    root_tasks: list[Task] | None = None,
    existing_task_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    task_artifact_updates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically enqueue new roots and make every root visible in one batch."""
    base_path = Path(base_dir)
    db_path = resolve_sqlite_path(base_path)
    ensure_database_initialized(db_path)
    tasks = list(root_tasks or [])
    task_ids = list(dict.fromkeys([
        *(task.task_id for task in tasks),
        *(str(task_id) for task_id in (existing_task_ids or []) if task_id),
    ]))
    if not task_ids:
        raise ValueError("task batch requires at least one root task")
    now = created_at or utc_now()
    batch_id = f"{batch_type}_{uuid4().hex[:12]}"
    repo = TaskRepository(str(db_path))
    with connect(db_path) as conn:
        conn.execute("begin immediate")
        repo.enqueue_many(tasks, conn=conn)
        task_storage.merge_artifacts(conn, task_artifact_updates or {}, updated_at=now)
        persist_task_batch(
            conn,
            batch_id=batch_id,
            batch_type=batch_type,
            task_ids=task_ids,
            source_ids=source_ids,
            metadata=metadata,
            created_at=now,
        )
    from backend.application.tasks.batches import get_task_batch

    return get_task_batch(base_path, batch_id)
