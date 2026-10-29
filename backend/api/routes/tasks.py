from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.read_models.task_manager import read_task_list
from backend.read_models.task_references import resolve_task_id
from backend.read_models.provider_recovery import provider_recovery_diagnostic

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/tasks")
def api_tasks(
    request: Request,
    status: str | None = None,
    task_type: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    chain_id: str | None = None,
    source_id: str | None = None,
    failure_class: str | None = None,
    started_from: str | None = None,
    started_to: str | None = None,
    limit: int = 100,
) -> JSONResponse:
    try:
        if status:
            TaskStatus(status)
        if task_type:
            TaskType(task_type)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    tasks = read_task_list(
        _base_dir(request),
        limit=max(1, min(int(limit), 1000)),
        status=status,
        task_type=task_type,
        target_kind=target_kind,
        target_id=target_id,
        chain_id=chain_id,
        source_id=source_id,
        failure_class=failure_class,
        started_from=started_from,
        started_to=started_to,
    )
    return JSONResponse({"tasks": tasks})


@router.post("/api/tasks/{task_id}/retry")
def api_retry_task(request: Request, task_id: str) -> JSONResponse:
    from backend.application.usecases.task_retry import retry_task

    base_dir = _base_dir(request)
    resolved_task_id = resolve_task_id(base_dir, task_id)
    if resolved_task_id is None:
        return JSONResponse(
            {"code": "task_not_found", "message": "Task not found"},
            status_code=404,
        )
    result = retry_task(base_dir, resolved_task_id)
    if not result["ok"]:
        code = result["code"]
        return JSONResponse(
            {"code": code, "message": _retry_error_message(code)},
            status_code=result["status_code"],
        )
    updated = TaskRepository.for_base_dir(base_dir).get(str(result["task_id"]))
    return JSONResponse(
        {
            **result,
            "task": _task_to_dict(updated, base_dir=base_dir),
            "recovery": provider_recovery_diagnostic(base_dir, updated),
        }
    )


@router.get("/api/tasks/{task_id}")
def api_task_detail(request: Request, task_id: str) -> JSONResponse:
    task = TaskRepository.for_base_dir(_base_dir(request)).get(task_id)
    if task is None:
        return JSONResponse({"ok": False, "error": "task not found"}, status_code=404)
    return JSONResponse(
        {
            "ok": True,
            "task": _task_to_dict(task, base_dir=_base_dir(request)),
        }
    )


def _task_to_dict(
    task: Task | None, *, base_dir: Path | str | None = None
) -> dict[str, Any]:
    if task is None:
        return {}
    payload = {
        "task_id": task.task_id,
        "task_type": task.task_type.value,
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
        "artifacts_summary": task.artifacts_summary,
    }
    if base_dir is not None:
        payload["recovery"] = provider_recovery_diagnostic(base_dir, task)
    return payload


def _retry_error_message(code: str) -> str:
    return {
        "task_not_found": "Task not found",
        "task_not_retryable": "Only failed or blocked tasks with remaining attempts can be retried",
        "task_stale_input": "Task input is stale",
        "provider_config_required": "Provider configuration is required",
    }.get(code, "Task is not retryable")
