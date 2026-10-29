from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.infrastructure.security.redaction import redact_secrets
from backend.read_models.operations_logs import (
    read_operations_log_detail,
    read_operations_log_page,
    valid_log_area,
    valid_log_level,
)

router = APIRouter()


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/logs")
def api_operations_logs(
    request: Request,
    query: str | None = None,
    level: str | None = None,
    area: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    object_id: str | None = None,
    request_id: str | None = None,
    limit: str = "25",
    offset: str = "0",
) -> JSONResponse:
    parsed_limit = _bounded_int(limit, lower=1, upper=100)
    if parsed_limit is None:
        return _error("invalid_log_pagination", "limit must be between 1 and 100", 422)
    parsed_offset = _bounded_int(offset, lower=0, upper=1_000_000)
    if parsed_offset is None:
        return _error("invalid_log_pagination", "offset must be non-negative", 422)
    if level and not valid_log_level(level):
        return _error("invalid_log_level", "Unsupported log level", 422)
    if area and not valid_log_area(area):
        return _error("invalid_log_area", "Unsupported log area", 422)
    if time_from and not _valid_timestamp(time_from):
        return _error("invalid_log_time", "time_from must be an ISO timestamp", 422)
    if time_to and not _valid_timestamp(time_to):
        return _error("invalid_log_time", "time_to must be an ISO timestamp", 422)
    payload = read_operations_log_page(
        _base_dir(request),
        query=query,
        level=level,
        area=area,
        time_from=time_from,
        time_to=time_to,
        object_id=object_id,
        request_id=request_id,
        limit=parsed_limit,
        offset=parsed_offset,
    )
    return JSONResponse(redact_secrets(payload))


@router.get("/api/logs/{log_id}")
def api_operations_log_detail(request: Request, log_id: str) -> JSONResponse:
    detail = read_operations_log_detail(_base_dir(request), log_id)
    if detail is None:
        return _error("log_not_found", "Log record not found", 404)
    return JSONResponse(redact_secrets(detail))


def _bounded_int(value: str, *, lower: int, upper: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if lower <= parsed <= upper else None


def _valid_timestamp(value: str) -> bool:
    from datetime import datetime

    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(redact_secrets({"code": code, "message": message}), status_code=status_code)
