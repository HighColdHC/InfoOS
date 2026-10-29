"""Application-facing task repository and transaction callbacks."""
from __future__ import annotations

from pathlib import Path

from backend.domain.tasks import Task, TaskStatus
from backend.infrastructure.storage.tasks.repository_core import TaskRepository as StorageTaskRepository
from backend.infrastructure.storage.tasks.finalization import TaskFinalizationQueue


class TaskRepository(StorageTaskRepository):
    """Preserve the public repository API while supplying workflow callbacks."""

    def recover_expired_leases(
        self, now: str, *, event_occurred_at: str | None = None,
        base_dir: str | Path | None = None,
    ) -> int:
        from backend.application.tasks.transitions import enqueue_downstream

        def enqueue_expired(
            queue: TaskFinalizationQueue, task: Task, directory: Path, timestamp: str,
        ) -> list[str]:
            return enqueue_downstream(
                queue, task, status=TaskStatus.FAILED,
                artifacts=dict(task.artifacts_summary), base_dir=directory,
            )
        return super().recover_expired_leases(
            now, event_occurred_at=event_occurred_at, base_dir=base_dir,
            enqueue_downstream=enqueue_expired,
        )
