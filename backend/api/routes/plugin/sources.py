"""Stable information-source catalog endpoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from backend.api.routes.plugin.dependencies import (
    error_response,
    require_cards_read,
    set_audit_meta,
)
from backend.read_models.plugin_sources import (
    InvalidSourcePageTokenError,
    read_plugin_source_page,
)


router = APIRouter()


@router.get("/sources")
def source_catalog(
    request: Request,
    page_size: str = "100",
    page_token: str | None = None,
    auth: Any = Depends(require_cards_read),
) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    try:
        parsed_size = int(page_size)
    except (TypeError, ValueError):
        parsed_size = 0
    if not 1 <= parsed_size <= 200:
        return error_response(
            request,
            "invalid_page_size",
            "page_size must be an integer between 1 and 200",
            status_code=422,
        )
    try:
        result = read_plugin_source_page(
            Path(request.app.state.base_dir),
            page_size=parsed_size,
            page_token=page_token,
        )
    except InvalidSourcePageTokenError:
        return error_response(
            request,
            "invalid_page_token",
            "Page token is invalid",
            status_code=422,
        )
    set_audit_meta(request, source_count=len(result["items"]), page_size=parsed_size)
    return result
