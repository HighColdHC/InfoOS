from __future__ import annotations

from backend.domain.timestamps import as_utc_datetime, format_utc_seconds_z
from backend.domain.timestamps import utc_now_seconds_z as now_utc_iso

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from backend.application.usecases.task_submission import submit_task_batch
from backend.application.tasks.planner import plan_manual_fetch_task_for_source
from backend.infrastructure.storage.repositories import SourceRepository
from backend.application.workflows.source_initial_fetch import enqueue_or_reuse_initial_fetch
from backend.read_models.source_run_list import (
    SourceRunPage,
    read_source_run_page,
    valid_execution_scope,
    valid_source_run_status,
    valid_task_view,
)
from backend.read_models.source_run_trace import read_source_run_trace

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/source-runs", response_model=None)
def api_source_runs(
    request: Request,
    query: str | None = Query(None, max_length=200),
    platform: str | None = Query(None, max_length=80),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1_000_000),
    time_from: datetime | None = Query(None),
    time_to: datetime | None = Query(None),
    status: str | None = Query(None, max_length=40),
    execution_scope: str = Query("all", max_length=20),
    task_view: str = Query("all", max_length=20),
) -> SourceRunPage:
    start = as_utc_datetime(time_from) if time_from is not None else None
    end = as_utc_datetime(time_to) if time_to is not None else None
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="time_from must be before or equal to time_to")
    if status and not valid_source_run_status(status):
        raise HTTPException(
            status_code=422,
            detail="status must be completed, attention, running or pending",
        )
    if not valid_execution_scope(execution_scope):
        raise HTTPException(
            status_code=422,
            detail="execution_scope must be all, executed or upcoming",
        )
    if not valid_task_view(task_view):
        raise HTTPException(
            status_code=422,
            detail="task_view must be all, collection or research",
        )
    return read_source_run_page(
        _base_dir(request),
        query=query,
        platform=platform,
        limit=limit,
        offset=offset,
        time_from=format_utc_seconds_z(start) if start is not None else None,
        time_to=format_utc_seconds_z(end) if end is not None else None,
        status=status,
        execution_scope=execution_scope,
        task_view=task_view,
    )






@router.get("/api/source-runs/{source_run_id}/trace")
def api_source_run_trace(
    request: Request,
    source_run_id: str,
    item_limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
) -> dict:
    try:
        payload = read_source_run_trace(
            _base_dir(request), source_run_id, item_limit=item_limit, cursor=cursor
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="source run not found")
    return payload


@router.post("/api/configurable-sources/{source_id}/run")
def api_run_configurable_source(request: Request, source_id: str) -> JSONResponse:
    base_dir = _base_dir(request)
    try:
        source = SourceRepository(base_dir).get_maintained_source(source_id)
        if source is None:
            return JSONResponse({"ok": False, "error": "source not found"}, status_code=404)
        current = now_utc_iso()
        if source["connector_status"] == "implemented_uncalibrated":
            queued = enqueue_or_reuse_initial_fetch(
                base_dir,
                source,
                now=current,
                reason="manual_first_fetch",
                retry_terminal=True,
            )
            task_id = queued.get("task_id")
            if not task_id:
                return JSONResponse({"ok": False, "error": "initial fetch unavailable"}, status_code=400)
            tasks = []
            existing_task_ids = [str(task_id)]
        else:
            manual_task = plan_manual_fetch_task_for_source(
                source,
                now=current,
            )
            if manual_task is None:
                return JSONResponse({"ok": False, "error": "manual fetch unavailable"}, status_code=400)
            tasks = [manual_task]
            existing_task_ids = []
        task_ids = [*(task.task_id for task in tasks), *existing_task_ids]
        batch = submit_task_batch(
            base_dir,
            batch_type="source_run",
            root_tasks=tasks,
            existing_task_ids=existing_task_ids,
            source_ids=[source_id],
            metadata={"planned_task_count": len(task_ids), "source_count": 1},
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "batch": {
                **batch,
                "progress_url": f"/api/task-batches/{batch['batch_id']}",
                "status_url": f"/api/task-batches/{batch['batch_id']}",
            },
        },
        status_code=202,
    )
