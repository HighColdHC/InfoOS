"""Compatibility entrypoints for the shared completion-driven task engine."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Iterable

from backend.application.tasks.execution_clock import env_int
from backend.application.tasks.registry import TaskHandler
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.tasks.lanes import ExecutionLane


def run_due_tasks(
    base_dir: str | Path,
    *,
    now: str | None = None,
    limit: int = 20,
    max_workers: int | None = None,
    task_types: Iterable[TaskType | str] | None = None,
    queue_names: Iterable[str] | None = None,
    worker_name: str | None = None,
    handlers: dict[TaskType, TaskHandler] | None = None,
) -> dict[str, Any]:
    from backend.application.tasks.continuous_executor import drain_once

    return drain_once(
        base_dir, now=now, limit=limit, max_workers=max_workers,
        task_types=task_types, queue_names=queue_names,
        worker_name=worker_name, handlers=handlers,
    )


def run_tasks_by_ids(
    base_dir: str | Path,
    task_ids: list[str],
    *,
    now: str | None = None,
    max_workers: int | None = None,
    task_types: Iterable[TaskType | str] | None = None,
    queue_names: Iterable[str] | None = None,
    worker_name: str | None = None,
    handlers: dict[TaskType, TaskHandler] | None = None,
) -> dict[str, Any]:
    from backend.application.tasks.continuous_executor import drain_once

    return drain_once(
        base_dir, task_ids=task_ids, now=now, limit=len(task_ids),
        max_workers=max_workers, task_types=task_types, queue_names=queue_names,
        worker_name=worker_name, handlers=handlers,
    )


def summarize_results(
    results: list[dict[str, Any]], *, max_workers: int | None, active_lanes: list[ExecutionLane],
    capacities: dict[ExecutionLane, int],
) -> dict[str, Any]:
    counts = {status: sum(item["status"] == status.value for item in results) for status in TaskStatus}
    return {
        "status": "success" if counts[TaskStatus.FAILED] == 0 else "partial_failure",
        "processed": len(results), "succeeded": counts[TaskStatus.SUCCESS],
        "failed": counts[TaskStatus.FAILED], "skipped": counts[TaskStatus.SKIPPED],
        "requeued": counts[TaskStatus.PENDING], "worker_count": resolve_worker_count(max_workers),
        "lane_worker_counts": {lane.value: capacities[lane] for lane in active_lanes}, "results": results,
    }


def empty_run_result(*, max_workers: int | None, recovered: int | None = None) -> dict[str, Any]:
    result = summarize_results([], max_workers=max_workers, active_lanes=[], capacities={})
    result["resource_pressure_deferred"] = 0
    result["resource_pressure_reasons"] = {}
    if recovered is not None:
        result["recovered"] = recovered
    return result


def resolve_worker_count(max_workers: int | None) -> int:
    if max_workers is not None:
        return max(1, int(max_workers))
    raw = os.environ.get("INFOOS_TASK_WORKERS")
    if not raw:
        return 1
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def public_fetch_worker_count() -> int:
    return max(1, env_int("INFOOS_PUBLIC_FETCH_WORKERS", 4))


def browser_fetch_worker_count() -> int:
    return max(1, env_int("INFOOS_BROWSER_FETCH_WORKERS", 2))


def task_type_values(task_types: Iterable[TaskType | str] | None) -> list[str] | None:
    if task_types is None:
        return None
    return [task_type.value if isinstance(task_type, TaskType) else str(task_type) for task_type in task_types]


def queue_name_values(queue_names: Iterable[str] | None) -> list[str] | None:
    if queue_names is None:
        return None
    return [
        str(getattr(queue_name, "value", queue_name))
        for queue_name in queue_names
    ]


def lease_duration_seconds() -> int:
    return max(2, env_int("INFOOS_TASK_LEASE_SECONDS", 300))


def new_worker_id(*, worker_name: str | None = None, queue_names: list[str] | None = None) -> str:
    role = worker_name or ("+".join(queue_names) if queue_names else "all")
    safe_role = "".join(char if char.isalnum() or char in "_-+" else "-" for char in role)
    return f"task-worker-{safe_role}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
