"""Asynchronous source batch HTTP routes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from backend.api._helpers import (
    _json_payload_or_empty,
)

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.post("/api/source-run-batches")
async def api_source_run_batch(request: Request) -> JSONResponse:
    base_dir = _base_dir(request)
    payload = await _json_payload_or_empty(request)
    from backend.application.workflows.source_batch import enqueue_source_batch

    source_ids = payload.get("source_ids")
    if source_ids is not None and not isinstance(source_ids, list):
        return JSONResponse({"ok": False, "error": "source_ids must be a list"}, status_code=400)
    try:
        batch = await run_in_threadpool(
            enqueue_source_batch,
            base_dir,
            source_ids=[str(source_id) for source_id in source_ids] if source_ids is not None else None,
            limit=int(payload.get("limit", 50)),
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
