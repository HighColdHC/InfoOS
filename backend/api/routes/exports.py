"""Task-backed data, backup, and diagnostic export HTTP routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from backend.api.schemas.exports import ExportRequestPayload
from backend.application.usecases.data_exports import estimate_export, submit_export
from backend.domain.export_contracts import EXPORT_KINDS, ExportRequestError
from backend.infrastructure.storage.export_jobs import resolve_export_artifact
from backend.read_models.exports import get_export_job, list_export_jobs


router = APIRouter(prefix="/api/exports", tags=["exports"])


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.post("/estimate")
def api_estimate_export(request: Request, payload: ExportRequestPayload) -> JSONResponse:
    try:
        result = estimate_export(_base_dir(request), payload.model_dump())
    except ExportRequestError as exc:
        return _error(exc.code, exc.message, status_code=422)
    return JSONResponse(result)


@router.post("")
def api_submit_export(request: Request, payload: ExportRequestPayload) -> JSONResponse:
    try:
        result = submit_export(_base_dir(request), payload.model_dump())
    except ExportRequestError as exc:
        return _error(exc.code, exc.message, status_code=422)
    return JSONResponse(result, status_code=202)


@router.get("")
def api_list_exports(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    limit: str = "25",
    offset: str = "0",
) -> JSONResponse:
    try:
        parsed_limit = int(limit)
        parsed_offset = int(offset)
        if not 1 <= parsed_limit <= 200 or parsed_offset < 0:
            raise ValueError("invalid pagination")
        if kind is not None and kind not in EXPORT_KINDS:
            raise ValueError("invalid export kind")
        result = list_export_jobs(
            _base_dir(request), limit=parsed_limit, offset=parsed_offset,
            kind=kind, status=status,
        )
    except ValueError as exc:
        return _error("invalid_export_filter", str(exc), status_code=422)
    return JSONResponse(result)


@router.get("/{task_id}/download")
def api_download_export(request: Request, task_id: str) -> Any:
    try:
        path, artifact = resolve_export_artifact(_base_dir(request), task_id)
    except LookupError:
        return _error("export_not_found", "Export task not found", status_code=404)
    except RuntimeError as exc:
        code = str(exc)
        status_code = 409 if code in {
            "export_not_ready", "export_failed", "export_artifact_invalid"
        } else 500
        return _error(code, "Export artifact is not available", status_code=status_code)
    media_type = "application/zip" if path.suffix == ".zip" else "application/json"
    return FileResponse(path, media_type=media_type, filename=str(artifact["file_name"]))


@router.get("/{task_id}")
def api_get_export(request: Request, task_id: str) -> JSONResponse:
    detail = get_export_job(_base_dir(request), task_id)
    if detail is None:
        return _error("export_not_found", "Export task not found", status_code=404)
    return JSONResponse(detail)


def _error(code: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)
