"""HTTP routes for durable multi-platform research batches."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse

from backend.api.schemas.research import ResearchRunBody
from backend.application.workflows.research import create_research_run
from backend.domain.research import ResearchRequestError
from backend.read_models.research_runs import read_research_run, read_research_runs

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.post("/api/research-runs")
def api_create_research_run(
    request: Request,
    body: ResearchRunBody,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> JSONResponse:
    try:
        research_run_id, replayed = create_research_run(
            _base_dir(request),
            payload=body.model_dump(mode="json"),
            idempotency_key=idempotency_key or "",
        )
        detail = read_research_run(_base_dir(request), research_run_id)
    except ResearchRequestError as exc:
        status = 409 if exc.code == "idempotency_conflict" else 422
        return _error(exc.code, exc.message, status=status, details=exc.details)
    if detail is None:
        return _error("research_run_not_found", "research run not found", status=404)
    return JSONResponse(
        {
            "ok": True,
            "replayed": replayed,
            "research_run": detail["research_run"],
        },
        status_code=200 if replayed else 202,
    )


@router.get("/api/research-runs")
def api_research_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
) -> dict:
    return {
        "ok": True,
        "research_runs": read_research_runs(_base_dir(request), limit=limit),
    }


@router.get("/api/research-runs/{research_run_id}")
def api_research_run_detail(
    request: Request,
    research_run_id: str,
    item_limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
) -> JSONResponse:
    try:
        payload = read_research_run(
            _base_dir(request),
            research_run_id,
            item_limit=item_limit,
            cursor=cursor,
        )
    except ValueError as exc:
        return _error("invalid_cursor", str(exc), status=400)
    if payload is None:
        return _error("research_run_not_found", "research run not found", status=404)
    return JSONResponse(payload)


def _error(
    code: str,
    message: str,
    *,
    status: int,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "details": details or {}},
        status_code=status,
    )
