"""Plugin capability and passive health endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from backend.api.routes.plugin.dependencies import require_cards_read, set_audit_meta
from backend.read_models.plugin_cards import CARD_SCHEMA
from backend.read_models.plugin_sources import SOURCE_SCHEMA


router = APIRouter()


@router.get("/capabilities")
def capabilities(request: Request, auth: Any = Depends(require_cards_read)) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    set_audit_meta(request)
    granted_capabilities = [
        scope
        for scope in ("cards:read", "assets:read")
        if scope in auth.scopes
    ]
    return {
        "interface_version": "v1",
        "card_schema": CARD_SCHEMA,
        "source_schema": SOURCE_SCHEMA,
        "capabilities": granted_capabilities,
        "default_page_size": 100,
        "max_page_size": 200,
        "catalog_filters": [
            "query",
            "platform",
            "completeness",
            "media_kind",
            "source_id",
        ],
        "catalog_fields": [
            "completeness_status",
            "excerpt",
            "source_url",
            "asset_summary",
            "source_id",
        ],
        "source_catalog_fields": [
            "source_id",
            "display_name",
            "platform",
            "source_type",
            "card_count",
            "latest_card_updated_at",
        ],
        "web_deep_links": False,
    }


@router.get("/health")
def health(request: Request, auth: Any = Depends(require_cards_read)) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    set_audit_meta(request)
    return {"status": "ready", "interface_version": "v1"}
