"""Normalize task artifacts when a retried task reaches a new terminal state."""
from __future__ import annotations

from typing import Any

from backend.application.tasks.types import Task, TaskStatus, TaskType

_VIDEO_FAILURE_KEYS = {"reason", "blocked_reason", "error", "quality_flags"}
_FETCH_FAILURE_KEYS = {
    "failure_reason",
    "failure_diagnostic",
    "connector_diagnostic",
    "user_action",
}


def merged_task_artifacts(
    task: Task,
    artifacts: dict[str, Any],
    *,
    status: TaskStatus | None = None,
) -> dict[str, Any]:
    merged = dict(task.artifacts_summary)
    stale_keys: set[str] = set()
    if task.task_type == TaskType.VIDEO_TRANSCRIBE and status == TaskStatus.SUCCESS:
        stale_keys = _VIDEO_FAILURE_KEYS
    elif task.task_type == TaskType.FETCH and status == TaskStatus.SUCCESS:
        stale_keys = _FETCH_FAILURE_KEYS
    merged = {key: value for key, value in merged.items() if key not in stale_keys}
    merged.update(artifacts)
    if task.task_type == TaskType.FETCH and status == TaskStatus.SUCCESS:
        merged = {key: value for key, value in merged.items() if key not in stale_keys}
    return merged
