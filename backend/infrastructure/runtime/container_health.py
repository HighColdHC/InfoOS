"""Passive projection of the Docker supervisor aggregate health artifact."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TypedDict

from backend.domain.json_types import JsonObject


class ContainerHealth(TypedDict):
    status: str
    reasons: list[str]
    checked_at: object


def read_container_health() -> ContainerHealth:
    path = Path(os.environ.get("INFOOS_CONTAINER_HEALTH_PATH", "/tmp/infoos-health-status.json"))
    try:
        payload: JsonObject = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown", "reasons": [], "checked_at": None}
    status = str(payload.get("status") or "unknown")
    reasons = [str(item) for item in payload.get("reasons", []) if str(item).strip()]
    return {
        "status": status if status in {"healthy", "unhealthy"} else "unknown",
        "reasons": reasons,
        "checked_at": payload.get("checked_at"),
    }
