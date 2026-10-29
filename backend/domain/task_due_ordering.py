"""Fair ordering for one bounded worker snapshot."""

from __future__ import annotations

from collections import deque

from backend.domain.tasks import Task, TaskType


_CORE_TYPES = frozenset(
    {
        TaskType.SOURCE_CALIBRATE,
        TaskType.FETCH,
        TaskType.CARD_BUILD,
        TaskType.CONTENT_ENRICH,
        TaskType.MEDIA_DOWNLOAD,
    }
)


def fair_due_snapshot(tasks: list[Task], limit: int | None) -> list[Task]:
    """Keep repair, core, notification, and optional work making progress."""
    if limit is None:
        return tasks
    size = max(0, int(limit))
    if size == 1:
        return tasks[:1]
    buckets = [deque(), deque(), deque(), deque()]
    for task in tasks:
        if task.task_type in {TaskType.PIPELINE_RECONCILE, TaskType.CARD_FINALIZE}:
            bucket = 0
        elif task.task_type in _CORE_TYPES:
            bucket = 1
        elif task.task_type in {TaskType.WEBHOOK_DELIVER, TaskType.NOTIFICATION_DELIVER, TaskType.AUTH_QR_CHECK}:
            bucket = 2
        else:
            bucket = 3
        buckets[bucket].append(task)
    buckets[0] = deque(
        sorted(
            buckets[0],
            key=lambda task: 0 if task.task_type is TaskType.PIPELINE_RECONCILE else 1,
        )
    )
    selected: list[Task] = []
    weighted_cycle = (0, 0, 1, 1, 1, 2, 3)
    while len(selected) < size and any(buckets):
        for bucket in weighted_cycle:
            if buckets[bucket] and len(selected) < size:
                selected.append(buckets[bucket].popleft())
    return selected
