"""Read-only platform capability catalog for API/UI consumers."""
from __future__ import annotations

from fastapi import APIRouter

from backend.domain.sources.registry import capability_catalog

router = APIRouter()


@router.get("/api/platform-capabilities")
def api_platform_capabilities() -> dict[str, object]:
    return {"platforms": capability_catalog()}
