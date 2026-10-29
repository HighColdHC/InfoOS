"""Durable task handler for task-event cold archiving."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus
from backend.application.workflows.task_event_archive import archive_task_events


def handle_task_event_archive(task: Task, base_dir: Path) -> dict[str, Any]:
    if task.target_kind != "global":
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"reason": f"task event archive does not support {task.target_kind} targets"},
        }
    return {
        "status": TaskStatus.SUCCESS.value,
        "result_version": task.input_version,
        "artifacts": archive_task_events(base_dir),
    }
