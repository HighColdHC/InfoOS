"""Authentication dependency and stable plugin error helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from backend.infrastructure.security.plugin_auth import PluginAuthError, authenticate_bearer


def require_cards_read(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Any:
    return _require_scope(request, authorization, "cards:read")


def require_assets_read(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Any:
    return _require_scope(request, authorization, "assets:read")


def require_captures_write(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Any:
    return _require_scope(request, authorization, "captures:write")


def error_response(
    request: Request,
    code: str,
    message: str,
    *,
    status_code: int,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "request_id": request_id(request)},
        status_code=status_code,
        headers=headers,
    )


def request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    if value:
        return str(value)
    generated = f"req_{uuid4().hex}"
    request.state.request_id = generated
    return generated


def set_audit_meta(request: Request, **values: Any) -> None:
    merged = dict(getattr(request.state, "audit_meta", {}))
    merged.update({
        key: value
        for key, value in values.items()
        if value is not None
    })
    request.state.audit_meta = merged


def _require_scope(request: Request, authorization: str | None, scope: str) -> Any:
    try:
        context = authenticate_bearer(
            Path(request.app.state.base_dir),
            authorization,
            required_scope=scope,
        )
    except PluginAuthError as exc:
        request.state.audit_result_code = exc.code
        request.state.audit_result_summary = str(exc)
        status_code = 403 if exc.code == "insufficient_scope" else 401
        return error_response(
            request,
            exc.code,
            str(exc),
            status_code=status_code,
        )
    request.state.plugin_auth = context
    set_audit_meta(request, required_scope=scope)
    return context
