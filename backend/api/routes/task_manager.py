from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.read_models.task_queue_tracking import task_tracking_page

router = APIRouter()


@router.get("/api/task-queue-tracking")
def api_task_queue_tracking(
    request: Request,
    limit: int = 100,
    offset: int = 0,
    queue_name: str | None = None,
    status: str | None = None,
) -> JSONResponse:
    normalized_limit = min(200, max(1, int(limit)))
    normalized_offset = max(0, int(offset))
    return JSONResponse(
        task_tracking_page(
            Path(request.app.state.base_dir),
            limit=normalized_limit,
            offset=normalized_offset,
            queue_name=queue_name,
            status=status,
        )
    )
