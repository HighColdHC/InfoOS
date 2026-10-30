"""Aggregator that wires every per-domain ``APIRouter`` into one
composable ``api_router``.

The web FastAPI app (``backend.app.main``) only needs to
include this single router to expose every backend HTTP endpoint.
Adding a new domain module is a one-liner: import it below and add
it to the ``router_modules`` tuple — the order is irrelevant
because routers carry their own prefixes.
"""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes import (
    auth_recovery,
    lan_account,
    browser,
    capture,
    card_preview,
    card_preview_thumbnails,
    opportunities,
    collector_status,
    collection_environment,
    cards,
    exports,
    intelligence_profile,
    intelligence_daily,
    intelligence_knowledge,
    logs,
    pages,
    platform_capabilities,
    plugin,
    reader_auth,
    management_auth,
    research_runs,
    runs,
    settings_browser,
    settings_notifications,
    setup,
    source_runs,
    sources,
    token_admin,
    task_batches,
    task_cancellations,
    tasks,
    task_manager,
)


api_router = APIRouter()
for _router_module in (
    pages,
    auth_recovery,
    lan_account,
    card_preview,
    card_preview_thumbnails,
    opportunities,
    collector_status,
    collection_environment,
    platform_capabilities,
    plugin,
    reader_auth,
    management_auth,
    research_runs,
    cards,
    intelligence_profile,
    intelligence_daily,
    intelligence_knowledge,
    source_runs,
    task_manager,
    task_batches,
    task_cancellations,
    exports,
    token_admin,
    sources,
    tasks,
    logs,
    runs,
    setup,
    settings_browser,
    settings_notifications,
    browser,
    capture,
):
    api_router.include_router(_router_module.router)


__all__ = ["api_router"]
