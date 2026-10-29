"""Passive collector status endpoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from backend.read_models.collector_status import read_collector_status


router = APIRouter()


@router.get("/api/collector-status")
def api_collector_status(request: Request) -> dict[str, Any]:
    return read_collector_status(Path(request.app.state.base_dir))


__all__ = ["router"]
