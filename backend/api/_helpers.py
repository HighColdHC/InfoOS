"""Shared payload models, request helpers, and internal route utilities."""
from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


async def _json_payload_or_empty(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body.strip():
        return {}
    try:
        payload = json.loads(body, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="request body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload
