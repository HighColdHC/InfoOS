"""Decoded API token rows; nullable columns follow collector migrations."""
from typing import TypedDict


class ApiTokenRow(TypedDict):
    token_id: str
    name: str
    token_prefix: str
    scopes: list[str]
    created_at: str
    revoked_at: str | None
    last_used_at: str | None
    replaced_by_token_id: str | None
    webhook_enabled: bool
    webhook_include_backfill: bool
    webhook_url: str | None
    webhook_config_generation: int
    webhook_delivery_status: str
    webhook_consecutive_failures: int
    webhook_last_attempt_at: str | None
    webhook_last_success_at: str | None
    webhook_last_failure_at: str | None
    webhook_last_error: str | None
    webhook_circuit_opened_at: str | None
    updated_at: str | None


class InternalApiTokenRow(ApiTokenRow):
    token_digest: str
    config_revision: int
