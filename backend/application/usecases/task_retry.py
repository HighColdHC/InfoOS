from __future__ import annotations


from pathlib import Path
from typing import Any

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.tasks.planner import now_utc_iso
from backend.application.workflows.source_initial_fetch import enqueue_or_reuse_initial_fetch
from backend.domain.retry_eligibility import is_task_retry_eligible
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


def retry_task(base_dir: Path | str, task_id: str) -> dict[str, Any]:
    repo = TaskRepository.for_base_dir(base_dir)
    task = repo.get(task_id)
    if task is None:
        return _error("task_not_found", 404)
    from backend.application.tasks.registry import RETIRED_TASK_TYPES
    if task.task_type in RETIRED_TASK_TYPES:
        return _error("feature_retired", 410)
    if task.task_type != TaskType.SOURCE_CALIBRATE:
        if (
            not is_task_retry_eligible(task)
            or not repo.retry(task_id)
        ):
            return _error("task_not_retryable", 409)
        _record_retry_scheduled(base_dir, task, repo.get(task_id))
        return {"ok": True, "task_id": task_id, "redirected": False}
    if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
        return {
            "ok": True,
            "task_id": task_id,
            "redirected": False,
            "reused_active_legacy": True,
        }
    source = SourceRepository(base_dir).get_source(task.target_id)
    if source is None:
        return _error("task_not_retryable", 409)
    queued = enqueue_or_reuse_initial_fetch(
        base_dir,
        source,
        now=now_utc_iso(),
        reason="legacy_calibration_retry",
        retry_terminal=True,
    )
    redirected_id = queued["task_id"]
    if not redirected_id:
        return _error("task_not_retryable", 409)
    _record_retry_scheduled(base_dir, task, repo.get(str(redirected_id)))
    return {
        "ok": True,
        "task_id": str(redirected_id),
        "redirected": True,
        "redirected_from": task_id,
    }


def _record_retry_scheduled(base_dir: Path | str, before, after) -> None:
    TaskEventRepository(base_dir).append(
        task_id=before.task_id,
        chain_id=str(before.artifacts_summary.get("chain_id") or "") or None,
        event_type="retry_scheduled",
        status=TaskStatus.PENDING.value,
        failure_class=before.last_failure_class,
        payload={
            "previous_status": before.status.value,
            "previous_attempt": before.attempt,
            "attempt": after.attempt if after is not None else before.attempt + 1,
            "trigger": "manual",
        },
    )


def _error(code: str, status_code: int) -> dict[str, Any]:
    return {"ok": False, "code": code, "status_code": status_code}
