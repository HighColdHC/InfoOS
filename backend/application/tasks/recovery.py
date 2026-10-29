"""Safe recovery decisions for non-failure task deferrals."""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.application.tasks.execution_clock import add_seconds
from backend.application.tasks.types import Task
from backend.domain.failures import RecoveryDecision

if TYPE_CHECKING:
    from backend.application.tasks.repository import TaskRepository


def defer_resource_contention(
    repo: TaskRepository,
    task: Task,
    *,
    worker_id: str,
    now: str,
    resource_kind: str,
    resource_key: str,
) -> RecoveryDecision | None:
    next_run_at = add_seconds(now, 1)
    reason = (
        "browser_site_busy"
        if resource_kind == "browser_site"
        else f"{resource_kind}_busy"
    )
    decision = RecoveryDecision(
        action="defer",
        reason=reason,
        next_run_at=next_run_at,
    )
    if not repo.defer_resource_contention(
        task,
        worker_id=worker_id,
        next_run_at=next_run_at,
        resource_kind=resource_kind,
        resource_key=resource_key,
        occurred_at=now,
        decision=decision,
    ):
        return None
    return decision
