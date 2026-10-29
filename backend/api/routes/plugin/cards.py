"""Stable information-card catalog and detail endpoints."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from backend.api.routes.plugin.dependencies import (
    error_response,
    require_cards_read,
    set_audit_meta,
)
from backend.read_models.plugin_cards import (
    InvalidPageTokenError,
    read_plugin_card_detail,
    read_plugin_card_page,
)


router = APIRouter()


@router.get("/cards")
def card_catalog(
    request: Request,
    page_size: str = "100",
    page_token: str | None = None,
    query: str | None = None,
    platform: str | None = None,
    completeness: str | None = None,
    media_kind: str | None = None,
    source_id: list[str] | None = Query(default=None),
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
    if query is not None and len(query.encode("utf-8")) > 2048:
        return error_response(
            request,
            "invalid_query",
            "query must not exceed 2048 UTF-8 bytes",
            status_code=422,
        )
    normalized_completeness = (completeness or "").strip().lower()
    if normalized_completeness and normalized_completeness not in {"complete", "partial"}:
        return error_response(
            request,
            "invalid_completeness",
            "completeness must be complete or partial",
            status_code=422,
        )
    normalized_media_kind = (media_kind or "").strip().lower()
    if normalized_media_kind and normalized_media_kind not in {"image", "video", "audio"}:
        return error_response(
            request,
            "invalid_media_kind",
            "media_kind must be image, video or audio",
            status_code=422,
        )
    normalized_source_ids = None
    if source_id is not None:
        normalized_source_ids = [value.strip() for value in source_id]
        if any(not value for value in normalized_source_ids):
            return error_response(
                request,
                "invalid_source_id",
                "source_id values must be non-empty",
                status_code=422,
            )
        if len(normalized_source_ids) > 100:
            return error_response(
                request,
                "too_many_source_ids",
                "source_id accepts at most 100 values",
                status_code=422,
            )
    try:
        result = read_plugin_card_page(
            Path(request.app.state.base_dir),
            page_size=parsed_size,
            page_token=page_token,
            query=query,
            platform=platform,
            completeness=normalized_completeness or None,
            media_kind=normalized_media_kind or None,
            source_ids=normalized_source_ids,
        )
    except InvalidPageTokenError:
        return error_response(
            request,
            "invalid_page_token",
            "Page token is invalid",
            status_code=422,
        )
    set_audit_meta(request, card_count=len(result["items"]), page_size=parsed_size)
    return result


@router.get("/cards/{card_id}")
def card_detail(
    request: Request,
    card_id: str,
    auth: Any = Depends(require_cards_read),
) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    detail = read_plugin_card_detail(Path(request.app.state.base_dir), card_id)
    if detail is None:
        return error_response(
            request,
            "card_not_found",
            "Card not found",
            status_code=404,
        )
    set_audit_meta(request, card_id=card_id, version=detail["card"]["version"])
    return detail
