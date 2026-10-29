from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.application.usecases.task_batch_retry import retry_task_batch
from backend.read_models.task_batches import (
    read_task_batch,
    read_task_batches,
)

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/task-batches")
def api_task_batches(request: Request, limit: int = 100) -> JSONResponse:
    return JSONResponse(
        {"ok": True, "batches": read_task_batches(_base_dir(request), limit=limit)}
    )


@router.get("/api/task-batches/{batch_id}")
def api_task_batch_detail(request: Request, batch_id: str) -> JSONResponse:
    try:
        batch = read_task_batch(_base_dir(request), batch_id)
    except KeyError:
        return JSONResponse({"ok": False, "error": "batch not found"}, status_code=404)
    return JSONResponse({"ok": True, "batch": batch})


@router.post("/api/task-batches/{batch_id}/retry")
def api_task_batch_retry(request: Request, batch_id: str) -> JSONResponse:
    try:
        batch = retry_task_batch(_base_dir(request), batch_id)
    except KeyError:
        return JSONResponse({"ok": False, "error": "batch not found"}, status_code=404)
    return JSONResponse({"ok": True, "batch": batch})
