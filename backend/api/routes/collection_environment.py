"""Read-only collection-environment projection."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from backend.infrastructure.runtime.environment import setup_state_path
from backend.read_models.collection_environment import read_collection_environment


router = APIRouter()
SETUP_STATE_PATH = setup_state_path()


@router.get("/api/collection-environment")
def get_collection_environment(request: Request) -> dict:
    return read_collection_environment(
        Path(request.app.state.base_dir),
        state_path=SETUP_STATE_PATH,
    )
