from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from backend.application.usecases.task_batch_cancel import cancel_tasks
from backend.application.usecases.task_cancel import cancel_task
from backend.read_models.task_references import resolve_task_id

router = APIRouter()
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]


class CancelRequest(BaseModel):
    reason: Reason


class BatchCancelRequest(CancelRequest):
    task_ids: list[str] = Field(min_length=1, max_length=100)


@router.post("/api/tasks/cancel")
def api_cancel_tasks(request: Request, body: BatchCancelRequest) -> JSONResponse:
    base_dir = Path(request.app.state.base_dir)
    task_ids = [resolve_task_id(base_dir, task_id) or task_id for task_id in body.task_ids]
    return JSONResponse(cancel_tasks(base_dir, task_ids, reason=body.reason))


@router.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(request: Request, task_id: str, body: CancelRequest) -> JSONResponse:
    base_dir = Path(request.app.state.base_dir)
    resolved = resolve_task_id(base_dir, task_id)
    if resolved is None:
        return JSONResponse(
            {"ok": False, "task_id": task_id, "code": "task_not_found", "message": "任务不存在"},
            status_code=404,
        )
    result = cancel_task(base_dir, resolved, reason=body.reason)
    if not result["ok"]:
        return JSONResponse(
            {**result, "message": "仅可取消 pending 或 blocked 任务"},
            status_code=result["status_code"],
        )
    return JSONResponse(result)
