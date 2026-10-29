"""Authenticated manual browser capture interface."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from backend.api.routes.capture_schemas import CaptureRequestV1
from backend.api.routes.plugin.dependencies import (
    error_response,
    request_id,
    require_captures_write,
    set_audit_meta,
)
from backend.application.usecases.capture_policy import (
    CAPTURE_LIMITS,
    capture_limit_error,
    has_unsafe_capture_url,
)
from backend.application.usecases.manual_capture import (
    CaptureIdempotencyConflict,
    read_manual_capture,
    submit_manual_capture,
)
from backend.infrastructure.security.capture_rate_limit import (
    admit_capture_request,
    capture_rate_limits,
)


router = APIRouter(prefix="/api/capture/v1")


@router.get("/capabilities")
def capabilities(
    request: Request,
    auth: Any = Depends(require_captures_write),
) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    token_limit, source_limit = capture_rate_limits()
    set_audit_meta(request)
    return {
        "interface_version": "capture.v1",
        "capabilities": ["captures:write", "url_only", "readable_content"],
        "limits": CAPTURE_LIMITS,
        "accepted_content_formats": ["readability_html"],
        "accepted_content_encodings": ["identity"],
        "rate_limits": {
            "token_requests_per_minute": token_limit,
            "source_requests_per_minute": source_limit,
        },
    }


@router.post("/items")
async def submit_item(
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth: Any = Depends(require_captures_write),
) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    source_address = str(request.client.host if request.client else "unknown")
    if not await run_in_threadpool(admit_capture_request, str(auth.token_id), source_address):
        return error_response(
            request,
            "capture_rate_limited",
            "Capture request rate limit exceeded",
            status_code=429,
            headers={"Retry-After": "60"},
        )
    content_encoding = str(request.headers.get("Content-Encoding") or "identity").lower()
    if content_encoding != "identity":
        return error_response(
            request,
            "unsupported_content_encoding",
            "Compressed capture requests are not supported",
            status_code=415,
        )
    declared_length = request.headers.get("Content-Length")
    if declared_length and declared_length.isdigit() and int(declared_length) > CAPTURE_LIMITS["request_bytes"]:
        return _too_large(request)
    body = await request.body()
    if len(body) > CAPTURE_LIMITS["request_bytes"]:
        return _too_large(request)
    try:
        decoded = json.loads(body.decode("utf-8"))
        capture = CaptureRequestV1.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError):
        return error_response(
            request,
            "invalid_capture_request",
            "Capture request does not match infoos.capture-request.v1",
            status_code=422,
        )
    if not idempotency_key or idempotency_key != capture.client_capture_id:
        return error_response(
            request,
            "idempotency_key_mismatch",
            "Idempotency-Key must match client_capture_id",
            status_code=422,
        )
    payload = capture.model_dump(mode="json", by_alias=True)
    limit_error = capture_limit_error(payload)
    if limit_error:
        return error_response(
            request,
            limit_error,
            "Capture field exceeds the declared server limit",
            status_code=413,
        )
    if await run_in_threadpool(has_unsafe_capture_url, payload):
        return error_response(
            request,
            "unsafe_capture_url",
            "Capture URL must be a public HTTP(S) URL",
            status_code=422,
        )
    try:
        submission = await run_in_threadpool(
            submit_manual_capture,
            Path(request.app.state.base_dir),
            token_id=str(auth.token_id),
            payload=payload,
        )
    except CaptureIdempotencyConflict:
        return error_response(
            request,
            "idempotency_conflict",
            "Idempotency key was already used for another payload",
            status_code=409,
        )
    receipt = {**submission.receipt, "request_id": request_id(request)}
    set_audit_meta(
        request,
        capture_id=receipt["capture_id"],
        capture_mode=payload["capture_mode"],
        request_bytes=len(body),
        disposition=receipt["disposition"],
    )
    return JSONResponse(receipt, status_code=submission.status_code)


@router.get("/items/{capture_id}")
def capture_receipt(
    request: Request,
    capture_id: str,
    auth: Any = Depends(require_captures_write),
) -> Any:
    if isinstance(auth, JSONResponse):
        return auth
    receipt = read_manual_capture(
        Path(request.app.state.base_dir),
        capture_id=capture_id,
        token_id=str(auth.token_id),
    )
    if receipt is None:
        return error_response(
            request,
            "capture_not_found",
            "Capture was not found",
            status_code=404,
        )
    set_audit_meta(
        request,
        capture_id=capture_id,
        processing_status=receipt["processing_status"],
    )
    return {**receipt, "request_id": request_id(request)}


def _too_large(request: Request) -> JSONResponse:
    return error_response(
        request,
        "capture_request_too_large",
        "Capture request exceeds the declared server limit",
        status_code=413,
    )
