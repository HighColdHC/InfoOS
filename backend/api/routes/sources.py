"""Configurable source maintenance HTTP routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.api.schemas.sources import (
    SourceEnabledPayload,
    SourcePayload,
    source_payload_dict,
)
from backend.application.usecases.source_activation import set_source_enabled
from backend.application.usecases.source_configuration_lifecycle import (
    DuplicateSourceError,
    ImmutableSourceIdentityError,
    SourceNotFoundError,
    create_source_configuration,
    update_source_configuration,
)
from backend.application.usecases.source_maintenance import (
    SourceHasHistoryError,
    delete_source_configuration,
)
from backend.domain.timestamps import utc_now_seconds_z
from backend.read_models.source_management import (
    read_source_detail,
    read_source_item,
    read_source_page,
    valid_source_filter_status,
)

router = APIRouter()

def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/api/configurable-sources")
def api_configurable_sources(
    request: Request,
    query: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    limit: str | None = None,
    offset: str | None = None,
) -> JSONResponse:
    parsed = _parse_pagination(limit, offset)
    if isinstance(parsed, JSONResponse):
        return parsed
    normalized_status = (status or "").strip()
    if normalized_status and not valid_source_filter_status(normalized_status):
        return _error(
            422,
            "invalid_source_status",
            "status must be healthy, needs_attention or disabled",
        )
    page = read_source_page(
        _base_dir(request),
        query=query,
        platform=platform,
        status=normalized_status,
        limit=parsed[0],
        offset=parsed[1],
    )
    return JSONResponse({**page, "sources": page["items"]})


@router.post("/api/configurable-sources")
def api_create_configurable_source(request: Request, payload: SourcePayload) -> JSONResponse:
    try:
        result = create_source_configuration(
            _base_dir(request),
            source_payload_dict(payload),
            now=utc_now_seconds_z(),
        )
    except DuplicateSourceError as exc:
        return _error(
            409,
            "duplicate_source",
            "Source already exists",
            existing_source_id=exc.existing_source_id,
        )
    except ValueError as exc:
        return _error(422, "invalid_source_configuration", str(exc))
    return JSONResponse(
        _project_lifecycle_result(_base_dir(request), result, legacy_source=bool(payload.source_id)),
        status_code=201,
    )


@router.get("/api/configurable-sources/{source_id}")
def api_configurable_source_detail(request: Request, source_id: str) -> JSONResponse:
    detail = read_source_detail(_base_dir(request), source_id)
    if detail is None:
        return _source_not_found()
    return JSONResponse(detail)


@router.put("/api/configurable-sources/{source_id}")
def api_update_configurable_source(
    request: Request,
    source_id: str,
    payload: SourcePayload,
) -> JSONResponse:
    try:
        result = update_source_configuration(
            _base_dir(request),
            source_id,
            source_payload_dict(payload),
            now=utc_now_seconds_z(),
            allow_identifier_change=payload.source_id == source_id,
        )
    except SourceNotFoundError:
        return _source_not_found()
    except ImmutableSourceIdentityError as exc:
        return _error(
            422,
            "immutable_source_identity",
            "platform, source_type and identifier cannot be changed",
            fields=list(exc.fields),
        )
    except ValueError as exc:
        return _error(422, "invalid_source_configuration", str(exc))
    return JSONResponse(
        _project_lifecycle_result(_base_dir(request), result, legacy_source=bool(payload.source_id))
    )


@router.patch("/api/configurable-sources/{source_id}/enabled")
def api_set_configurable_source_enabled(
    request: Request,
    source_id: str,
    payload: SourceEnabledPayload,
) -> JSONResponse:
    try:
        result = set_source_enabled(_base_dir(request), source_id, payload.enabled)
    except SourceNotFoundError:
        return _source_not_found()
    return JSONResponse(_project_lifecycle_result(_base_dir(request), result))


@router.delete("/api/configurable-sources/{source_id}")
def api_delete_configurable_source(request: Request, source_id: str) -> JSONResponse:
    try:
        delete_source_configuration(_base_dir(request), source_id)
    except SourceNotFoundError:
        return _source_not_found()
    except SourceHasHistoryError:
        return _error(
            409,
            "source_has_history",
            "Source with persisted history cannot be deleted; disable it instead",
        )
    return JSONResponse({"deleted": True, "source_id": source_id})


def _parse_pagination(limit: str | None, offset: str | None) -> tuple[int, int] | JSONResponse:
    try:
        parsed_limit = 25 if limit is None else int(limit)
    except (TypeError, ValueError):
        return _error(422, "invalid_pagination", "limit must be an integer between 1 and 100")
    if not 1 <= parsed_limit <= 100:
        return _error(422, "invalid_pagination", "limit must be an integer between 1 and 100")
    try:
        parsed_offset = 0 if offset is None else int(offset)
    except (TypeError, ValueError):
        return _error(422, "invalid_pagination", "offset must be a non-negative integer")
    if parsed_offset < 0:
        return _error(422, "invalid_pagination", "offset must be a non-negative integer")
    return parsed_limit, parsed_offset


def _project_lifecycle_result(
    base_dir: Path,
    result: dict[str, Any],
    *,
    legacy_source: bool = False,
) -> dict[str, Any]:
    if legacy_source:
        if result["task_id"] is None:
            result = {
                **result,
                "calibration_task_id": None,
                "initial_fetch_task_id": None,
                "initial_fetch_queue_status": None,
                "queue_status": "ready"
                if result["source"]["connector_status"] in {"supported", "partial"}
                else result["queue_status"],
                "task_ids": [],
            }
        return result
    item = read_source_item(base_dir, result["source_id"])
    return {**result, "source": item if item else result["source"]}


def _source_not_found() -> JSONResponse:
    return _error(404, "source_not_found", "Source not found")


def _error(status_code: int, code: str, message: str, **details: Any) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, **details}, status_code=status_code)
