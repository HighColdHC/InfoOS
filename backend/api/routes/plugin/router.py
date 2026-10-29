"""Versioned plugin interface router."""
from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes.plugin.assets import router as assets_router
from backend.api.routes.plugin.capabilities import router as capabilities_router
from backend.api.routes.plugin.cards import router as cards_router
from backend.api.routes.plugin.sources import router as sources_router


router = APIRouter(prefix="/api/plugin/v1", tags=["plugin-interface"])
router.include_router(capabilities_router)
router.include_router(sources_router)
router.include_router(cards_router)
router.include_router(assets_router)
