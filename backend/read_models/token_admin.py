"""Read-only projections for Token administration and request audit logs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from backend.infrastructure.storage.api_request_logs import (
    ApiRequestLogRepository, ApiRequestLogDetail, ApiRequestLogPage, ApiRequestLogSummary,
)
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.storage.api_tokens_types import ApiTokenRow


class _LogFilters(TypedDict):
    query: str | None
    status: str | int | None
    created_from: str | None
    created_to: str | None


class TokenOverview(TypedDict):
    interface_path: str
    interface_version: str
    service_status: str
    capabilities: list[str]


class TokenItem(ApiTokenRow):
    status: str


class RequestLogPage(ApiRequestLogPage):
    summary: ApiRequestLogSummary


INTERFACE_PATH = "/api/plugin/v1"
INTERFACE_VERSION = "v1"
INTERFACE_CAPABILITIES = [
    "cards:read", "assets:read", "captures:write",
    "intelligence:read", "profile:write", "evaluation:write", "feedback:write",
    "collect:write", "sources:write", "verification:write",
]


def read_token_overview(_base_dir: Path | str) -> TokenOverview:
    return {
        "interface_path": INTERFACE_PATH,
        "interface_version": INTERFACE_VERSION,
        "service_status": "ready",
        "capabilities": list(INTERFACE_CAPABILITIES),
    }


def read_tokens(base_dir: Path | str) -> list[TokenItem]:
    return [present_token(item) for item in ApiTokenRepository(base_dir).list_tokens()]


def read_request_logs(
    base_dir: Path | str,
    *,
    query: str | None,
    status: str | int | None,
    time_range: str,
    limit: int,
    offset: int,
) -> RequestLogPage:
    created_from = time_range_start(time_range)
    repository = ApiRequestLogRepository(base_dir)
    filters: _LogFilters = {
        "query": query,
        "status": status,
        "created_from": created_from,
        "created_to": None,
    }
    page = repository.list(limit=limit, offset=offset, **filters)
    return {"summary": repository.summary(**filters), **page}


def read_request_log_detail(base_dir: Path | str, request_id: str) -> ApiRequestLogDetail | None:
    return ApiRequestLogRepository(base_dir).get_detail(request_id)


def present_token(token: ApiTokenRow) -> TokenItem:
    replaced = bool(token["replaced_by_token_id"])
    revoked = bool(token["revoked_at"])
    return {
        "token_id": token["token_id"],
        "name": token["name"],
        "token_prefix": token["token_prefix"],
        "scopes": token["scopes"],
        "created_at": token["created_at"],
        "revoked_at": token["revoked_at"],
        "last_used_at": token["last_used_at"],
        "replaced_by_token_id": token["replaced_by_token_id"],
        "webhook_enabled": token["webhook_enabled"],
        "webhook_include_backfill": token["webhook_include_backfill"],
        "webhook_url": token["webhook_url"],
        "webhook_config_generation": token["webhook_config_generation"],
        "webhook_delivery_status": token["webhook_delivery_status"],
        "webhook_consecutive_failures": token["webhook_consecutive_failures"],
        "webhook_last_attempt_at": token["webhook_last_attempt_at"],
        "webhook_last_success_at": token["webhook_last_success_at"],
        "webhook_last_failure_at": token["webhook_last_failure_at"],
        "webhook_last_error": token["webhook_last_error"],
        "webhook_circuit_opened_at": token["webhook_circuit_opened_at"],
        "updated_at": token["updated_at"],
        "status": "replaced" if replaced else "revoked" if revoked else "active",
    }


def time_range_start(value: str) -> str:
    duration = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}.get(
        value.strip().lower()
    )
    if duration is None:
        raise ValueError("unsupported time range")
    start = datetime.now(timezone.utc) - duration
    return start.replace(microsecond=0).isoformat().replace("+00:00", "Z")
