"""Finite candidate snapshot and result collection for the shared task engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.application.tasks.executor import summarize_results
from backend.application.tasks.lanes import ExecutionLane
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task


@dataclass
class SynchronousBatch:
    limit: int
    now: str | None
    task_ids: list[str] | None
    task_types: list[str] | None
    queue_names: list[str] | None
    snapshot: list[Task] = field(default_factory=list)
    considered: set[str] = field(default_factory=set)
    results: list[dict[str, Any]] = field(default_factory=list)
    active_lanes: set[ExecutionLane] = field(default_factory=set)
    submitted: int = 0

    def load(self, repo: TaskRepository, snapshot_at: str) -> None:
        if self.task_ids is None:
            # Freeze membership: descendants enqueued by handlers belong to the
            # next call. Browser backlog is scanned by the shared refill loop.
            self.snapshot = repo.list_due(
                now=snapshot_at,
                limit=None if self.queue_names == ["browser_fetch"] else self.limit,
                task_types=self.task_types, queue_names=self.queue_names,
            )
        else:
            self.snapshot = [task for task_id in dict.fromkeys(self.task_ids)
                             if (task := repo.get(task_id)) is not None]

    def page(self, *, limit: int, after: int | None) -> tuple[list[Task], int | None]:
        offset = after or 0
        page = self.snapshot[offset:offset + limit]
        end = offset + len(page)
        return page, end if end < len(self.snapshot) else None

    def eligible(self, task: Task) -> bool:
        return (self.submitted < self.limit and task.task_id not in self.considered
                and (self.task_types is None or task.task_type.value in self.task_types)
                and (self.queue_names is None or task.queue_name in self.queue_names))

    def summarize(self, *, max_workers, capacities, counts, reasons) -> dict[str, Any]:
        result = summarize_results(
            self.results, max_workers=max_workers,
            active_lanes=[lane for lane in ExecutionLane if lane in self.active_lanes],
            capacities=capacities,
        )
        result["resource_pressure_deferred"] = counts["resource_pressure_deferred"]
        result["resource_pressure_reasons"] = dict(reasons)
        if self.task_ids is None:
            result["recovered"] = counts["recovered"]
        return result
