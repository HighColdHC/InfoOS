"""Browser visibility HTTP route.

GET /api/browser/visibility            # current value
PUT /api/browser/visibility            # body: {"headless_during_fetch": bool}

The value is read on every fetch by ``OpenCLIConnector``; the PUT
is a no-op if the value matches the stored value (visibility.write
is idempotent).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field, StrictBool

from backend.infrastructure.runtime.environment import config_yaml_path
from backend.application.setup import visibility

router = APIRouter(prefix="/api/browser", tags=["browser"])


def _resolve_config_path():
    return config_yaml_path()


class VisibilityPayload(BaseModel):
    # Pydantic v2 coerces "yes"/"no" to bool for Field(...). Use StrictBool so
    # the API actually rejects non-bool inputs with 422 (matches the test).
    headless_during_fetch: StrictBool = Field(...)


@router.get("/visibility")
def get_visibility() -> dict[str, bool]:
    return {"headless_during_fetch": visibility.read_headless_during_fetch(_resolve_config_path())}


@router.put("/visibility", status_code=204)
def put_visibility(payload: VisibilityPayload) -> None:
    visibility.write_headless_during_fetch(payload.headless_during_fetch, _resolve_config_path())
    return None
