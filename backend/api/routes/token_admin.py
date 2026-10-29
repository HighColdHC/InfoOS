"""HTTP management surface for plugin tokens and request audit evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.application.usecases.api_tokens import (
    ApiTokenNotFoundError,
    ApiTokenRevokedError,
    ApiTokenConfigurationChangedError,
    WebhookConnectionError,
    create_token,
    replay_failed_webhooks,
    revoke_token,
    rotate_token,
    test_token_webhook,
    update_token_webhook,
)
from backend.infrastructure.security.webhook_urls import UnsafeWebhookUrlError
from backend.read_models.token_admin import (
    present_token,
    read_request_log_detail,
    read_request_logs,
    read_token_overview,
    read_tokens,
)


router = APIRouter(prefix="/api/token-auth")


class CreateTokenPayload(BaseModel):
    name: str
    scopes: list[str]
    webhook_enabled: bool = False
    webhook_include_backfill: bool = False
    webhook_url: str | None = None


class UpdateTokenPayload(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    webhook_enabled: bool
    webhook_include_backfill: bool | None = None
    webhook_url: str | None = None


class ReplayWebhookPayload(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)


def _base_dir(request: Request) -> Path:
    return Path(request.app.state.base_dir)


@router.get("/overview")
def token_overview(request: Request) -> dict[str, Any]:
    return read_token_overview(_base_dir(request))


@router.get("/tokens")
def token_list(request: Request) -> dict[str, Any]:
    return {"items": read_tokens(_base_dir(request))}


@router.post("/tokens")
def token_create(request: Request, payload: CreateTokenPayload) -> JSONResponse:
    try:
        result = create_token(
            _base_dir(request),
            name=payload.name,
            scopes=payload.scopes,
            webhook_enabled=payload.webhook_enabled,
            webhook_include_backfill=payload.webhook_include_backfill,
            webhook_url=payload.webhook_url,
            verify_webhook=True,
        )
    except UnsafeWebhookUrlError as exc:
        return _error("unsafe_webhook_url", str(exc), status_code=422)
    except WebhookConnectionError as exc:
        return _error("webhook_unreachable", str(exc), status_code=422)
    except ValueError as exc:
        return _error("invalid_token_request", str(exc), status_code=422)
    return JSONResponse(
        {"plaintext_token": result["plaintext_token"], "token": present_token(result["token"])},
        status_code=201,
    )


@router.patch("/tokens/{token_id}")
def token_update(request: Request, token_id: str, payload: UpdateTokenPayload) -> JSONResponse:
    try:
        token = update_token_webhook(
            _base_dir(request),
            token_id,
            name=payload.name,
            scopes=payload.scopes,
            webhook_enabled=payload.webhook_enabled,
            webhook_include_backfill=payload.webhook_include_backfill,
            webhook_url=payload.webhook_url,
            verify_webhook=True,
        )
    except ApiTokenNotFoundError:
        return _error("token_not_found", "API token not found", status_code=404)
    except ApiTokenRevokedError:
        return _error("token_revoked", "API token is revoked", status_code=409)
    except ApiTokenConfigurationChangedError as exc:
        return _error("token_configuration_changed", str(exc), status_code=409)
    except UnsafeWebhookUrlError as exc:
        return _error("unsafe_webhook_url", str(exc), status_code=422)
    except WebhookConnectionError as exc:
        return _error("webhook_unreachable", str(exc), status_code=422)
    except ValueError as exc:
        return _error("invalid_token_request", str(exc), status_code=422)
    return JSONResponse(present_token(token))


@router.post("/tokens/{token_id}/webhook-test")
def token_webhook_test(request: Request, token_id: str) -> JSONResponse:
    try:
        result = test_token_webhook(_base_dir(request), token_id)
    except ApiTokenNotFoundError:
        return _error("token_not_found", "API token not found", status_code=404)
    except ApiTokenRevokedError:
        return _error("token_revoked", "API token is revoked", status_code=409)
    except UnsafeWebhookUrlError as exc:
        return _error("unsafe_webhook_url", str(exc), status_code=422)
    except WebhookConnectionError as exc:
        return _error("webhook_unreachable", str(exc), status_code=422)
    except ValueError as exc:
        return _error("webhook_inactive", str(exc), status_code=409)
    result["token"] = present_token(result["token"])
    return JSONResponse(result)


@router.post("/tokens/{token_id}/webhook-replay")
def token_webhook_replay(
    request: Request,
    token_id: str,
    payload: ReplayWebhookPayload,
) -> JSONResponse:
    try:
        result = replay_failed_webhooks(
            _base_dir(request),
            token_id,
            limit=payload.limit,
        )
    except ApiTokenNotFoundError:
        return _error("token_not_found", "API token not found", status_code=404)
    except ApiTokenRevokedError:
        return _error("token_revoked", "API token is revoked", status_code=409)
    except ValueError as exc:
        return _error("webhook_circuit_open", str(exc), status_code=409)
    return JSONResponse(result)


@router.post("/tokens/{token_id}/revoke")
def token_revoke(request: Request, token_id: str) -> JSONResponse:
    token = revoke_token(_base_dir(request), token_id)
    if token is None:
        return _error("token_not_found", "API token not found", status_code=404)
    return JSONResponse(present_token(token))


@router.post("/tokens/{token_id}/rotate")
def token_rotate(request: Request, token_id: str) -> JSONResponse:
    try:
        result = rotate_token(_base_dir(request), token_id, verify_webhook=True)
    except ApiTokenNotFoundError:
        return _error("token_not_found", "API token not found", status_code=404)
    except ApiTokenRevokedError:
        return _error("token_revoked", "API token is revoked", status_code=409)
    except ApiTokenConfigurationChangedError as exc:
        return _error("token_configuration_changed", str(exc), status_code=409)
    except WebhookConnectionError as exc:
        return _error("webhook_unreachable", str(exc), status_code=422)
    return JSONResponse(
        {"plaintext_token": result["plaintext_token"], "token": present_token(result["token"])},
        status_code=201,
    )


@router.get("/request-logs")
def request_log_list(
    request: Request,
    query: str | None = None,
    status: str | None = None,
    time_range: str = "24h",
    limit: str = "25",
    offset: str = "0",
) -> JSONResponse:
    try:
        parsed_limit = int(limit)
        parsed_offset = int(offset)
        if not 1 <= parsed_limit <= 100 or parsed_offset < 0:
            raise ValueError("invalid pagination")
        payload = read_request_logs(
            _base_dir(request),
            query=query,
            status=status,
            time_range=time_range,
            limit=parsed_limit,
            offset=parsed_offset,
        )
    except ValueError as exc:
        return _error("invalid_request_log_filter", str(exc), status_code=422)
    return JSONResponse(payload)


@router.get("/request-logs/{request_id}")
def request_log_detail(request: Request, request_id: str) -> JSONResponse:
    detail = read_request_log_detail(_base_dir(request), request_id)
    if detail is None:
        return _error("request_log_not_found", "Request log not found", status_code=404)
    return JSONResponse(detail)


def _error(code: str, message: str, *, status_code: int) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status_code)
