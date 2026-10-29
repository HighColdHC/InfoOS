"""Idempotent local-day brief planning."""
from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.domain.timestamps import parse_utc_or_now


def enqueue_daily_brief_build_task(
    base_dir: str | Path, *, now: str, repo: TaskRepository | None = None,
) -> str | None:
    local = parse_utc_or_now(now).astimezone(ZoneInfo("Asia/Shanghai"))
    if local.hour < 7:
        return None
    brief_date = local.date().isoformat()
    task = Task(
        task_id=f"daily_brief_build_{brief_date}", task_type=TaskType.DAILY_BRIEF_BUILD,
        target_kind="brief", target_id=brief_date, next_run_at=now,
        input_version=brief_date, artifacts_summary={"brief_date": brief_date},
    )
    (repo or TaskRepository.for_base_dir(base_dir)).enqueue(task)
    return task.task_id
