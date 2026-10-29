from __future__ import annotations

from backend.domain.timestamps import parse_utc_or_none

from datetime import datetime, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

from backend.domain.json_types import JsonObject
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.domain.retry_eligibility import is_task_retry_eligible
from backend.infrastructure.storage.task_queries import TaskReadQuery
from backend.read_models.operations import TaskOperation, classify_task_operation
from backend.read_models.task_manager_context import (
    CardContext,
    card_context_by_id,
    source_by_card_id,
    task_card_id,
)
from backend.read_models.task_manager_operations import (
    TaskProgress,
    next_action_label,
    progress,
    task_error_message,
)
from backend.read_models.provider_recovery import ProviderRecovery, provider_recovery_diagnostic
from backend.read_models.task_queue_tracking import task_tracking


class TaskDependency(TypedDict):
    task_id: str
    required_status: str


class TaskItem(TypedDict):
    task_id: str
    task_type: str
    display_stage: str
    source_id: str | None
    chain_id: object
    chain_root_task_id: object
    input_version: str
    result_version: str | None
    claimed_by: str | None
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    wait_seconds: int
    overdue: bool
    lease_expires_at: str | None
    heartbeat_at: str | None
    started_at: str | None
    finished_at: str | None
    last_failure_class: str | None
    retry_at: str | None
    stuck: bool
    duration_seconds: int | None
    upstream_task_id: object
    downstream_task_ids: list[object]
    target_kind: str
    target_id: str
    status: str
    depends_on: list[TaskDependency]
    attempt: int
    max_attempts: int
    next_run_at: str | None
    error_reason: str | None
    error_message: str | None
    artifacts_summary: JsonObject
    progress: TaskProgress
    summary: JsonObject
    operation: TaskOperation
    next_action_label: str
    retryable: bool
    recovery: ProviderRecovery
    card_context: NotRequired[CardContext]


def read_task_list(
    base_dir: Path | str,
    *,
    limit: int = 100,
    now: str | None = None,
    **filters: str | None,
) -> list[TaskItem]:
    """Project the tasks matching ``TaskReadQuery.search_tasks`` filters, newest first."""
    base_path = Path(base_dir)
    tasks = TaskReadQuery(base_path).search_tasks(limit=limit, **filters)
    current = parse_utc_or_none(now) or datetime.now(timezone.utc)
    source_by_card = source_by_card_id(base_path, tasks)
    card_contexts = card_context_by_id(base_path, tasks)
    return [
        _task_to_dict(
            task,
            base_dir=base_path,
            source_by_card=source_by_card,
            card_context_by_id=card_contexts,
            current=current,
        )
        for task in tasks
    ]


def _task_to_dict(
    task: Task,
    *,
    base_dir: Path,
    source_by_card: dict[str, str],
    card_context_by_id: dict[str, CardContext],
    current: datetime,
) -> TaskItem:
    operation = classify_task_operation(task)
    error_message = task_error_message(task)
    card_id = task_card_id(task)
    recovery = provider_recovery_diagnostic(base_dir, task)
    payload: TaskItem = {
        "task_id": task.task_id,
        "task_type": task.task_type.value,
        "display_stage": _display_stage(task),
        "source_id": _task_source_id(task, source_by_card),
        "chain_id": task.artifacts_summary.get("chain_id"),
        "chain_root_task_id": task.artifacts_summary.get("chain_root_task_id"),
        "input_version": task.input_version,
        "result_version": task.result_version,
        "claimed_by": task.claimed_by,
        **task_tracking(task, evaluated_at=_format_current(current)),
        "lease_expires_at": task.lease_expires_at,
        "heartbeat_at": task.heartbeat_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "last_failure_class": task.last_failure_class,
        "retry_at": task.next_run_at,
        "stuck": _is_stuck(task, current),
        "duration_seconds": _duration_seconds(task, current),
        "upstream_task_id": task.artifacts_summary.get("upstream_task_id"),
        "downstream_task_ids": list(task.artifacts_summary.get("downstream_task_ids") or []),
        "target_kind": task.target_kind,
        "target_id": task.target_id,
        "status": task.status.value,
        "depends_on": [
            {"task_id": dep.task_id, "required_status": dep.required_status.value}
            for dep in task.depends_on
        ],
        "attempt": task.attempt,
        "max_attempts": task.max_attempts,
        "next_run_at": task.next_run_at,
        "error_reason": task.error_reason,
        "error_message": error_message,
        "artifacts_summary": task.artifacts_summary,
        "progress": progress(task),
        "summary": dict(task.artifacts_summary.get("summary") or {}),
        "operation": operation,
        "next_action_label": next_action_label(task, operation),
        "retryable": _task_retryable(base_dir, task, recovery=recovery),
        "recovery": recovery,
    }
    if card_id and card_id in card_context_by_id:
        payload["card_context"] = card_context_by_id[card_id]
    return payload


def _format_current(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _display_stage(task: Task) -> str:
    if task.task_type == TaskType.FETCH and task.artifacts_summary.get("initial_fetch") is True:
        return "initial_fetch"
    if task.task_type == TaskType.SOURCE_CALIBRATE:
        return "source_calibrate_history"
    return task.task_type.value


def _task_retryable(
    base_dir: Path,
    task: Task,
    *,
    recovery: ProviderRecovery | None = None,
) -> bool:
    """Project the same retry action the task API can actually accept."""
    if task.task_type == TaskType.CARD_LLM_ENRICH:
        diagnostic = recovery if recovery is not None else provider_recovery_diagnostic(base_dir, task)
        return diagnostic["manual_retry_allowed"]
    return is_task_retry_eligible(task)


def _is_stuck(task: Task, current: datetime) -> bool:
    lease = parse_utc_or_none(task.lease_expires_at)
    return task.status == TaskStatus.RUNNING and lease is not None and lease < current


def _duration_seconds(task: Task, current: datetime) -> int | None:
    started = parse_utc_or_none(task.started_at)
    if started is None:
        return None
    finished = parse_utc_or_none(task.finished_at) or (current if task.status == TaskStatus.RUNNING else None)
    if finished is None:
        return None
    return max(0, int((finished - started).total_seconds()))


def _task_source_id(task: Task, source_by_card: dict[str, str]) -> str | None:
    if task.target_kind == "source" and task.target_id:
        return task.target_id
    card_id = task_card_id(task)
    return source_by_card.get(card_id) if card_id else None
