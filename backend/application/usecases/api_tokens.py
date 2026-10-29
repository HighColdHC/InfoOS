"""Create, revoke, and rotate read-only plugin API tokens."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import secrets
from typing import Any
from uuid import uuid4

from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.security.plugin_auth import VERIFICATION_WRITE, SOURCES_WRITE, COLLECT_WRITE, FEEDBACK_WRITE, EVALUATION_WRITE, INTELLIGENCE_READ, PROFILE_WRITE
from backend.infrastructure.security.webhook_urls import validate_webhook_url
from backend.infrastructure.webhooks.delivery import WebhookTransportError, probe_webhook


ALLOWED_SCOPES = frozenset({
    "cards:read", "assets:read", "captures:write",
    INTELLIGENCE_READ, PROFILE_WRITE, FEEDBACK_WRITE, EVALUATION_WRITE, COLLECT_WRITE, SOURCES_WRITE, VERIFICATION_WRITE,
})


class WebhookConnectionError(ValueError):
    pass


class ApiTokenNotFoundError(LookupError):
    def __init__(self, token_id: str):
        super().__init__("API token not found")
        self.token_id = token_id


class ApiTokenRevokedError(ValueError):
    def __init__(self, token_id: str):
        super().__init__("API token is revoked")
        self.token_id = token_id


class ApiTokenConfigurationChangedError(RuntimeError):
    pass


def create_token(
    base_dir: Path | str,
    *,
    name: str,
    scopes: list[str] | tuple[str, ...],
    now: str | None = None,
    webhook_enabled: bool = False,
    webhook_include_backfill: bool = False,
    webhook_url: str | None = None,
    verify_webhook: bool = False,
) -> dict[str, Any]:
    normalized_name = str(name).strip()
    if not normalized_name:
        raise ValueError("token name is required")
    normalized_scopes = _validated_scopes(scopes)
    _validate_webhook_scope(normalized_scopes, webhook_enabled)
    material = _new_token_material()
    normalized_webhook_url = _validated_webhook(webhook_enabled, webhook_url)
    stored = ApiTokenRepository(base_dir).append(
        token_id=material["token_id"],
        name=normalized_name,
        token_prefix=material["token_prefix"],
        token_digest=material["token_digest"],
        scopes=normalized_scopes,
        created_at=now,
        webhook_enabled=webhook_enabled,
        webhook_include_backfill=webhook_include_backfill,
        webhook_url=normalized_webhook_url,
        # A newly generated signing token has not yet been shown to the
        # receiver owner. Production creation therefore uses a two-phase
        # handshake: persist as pending, reveal once, then require the signed
        # webhook-test endpoint to activate delivery.
        webhook_initial_status=(
            "pending_verification"
            if verify_webhook and webhook_enabled and normalized_webhook_url
            else None
        ),
    )
    return {
        "plaintext_token": material["plaintext_token"],
        "token": _public_token(stored),
    }


def revoke_token(
    base_dir: Path | str,
    token_id: str,
    *,
    now: str | None = None,
) -> dict[str, Any] | None:
    stored = ApiTokenRepository(base_dir).revoke(token_id, revoked_at=now)
    return _public_token(stored) if stored is not None else None


def update_token_webhook(
    base_dir: Path | str,
    token_id: str,
    *,
    name: str | None = None,
    scopes: list[str] | tuple[str, ...] | None = None,
    webhook_enabled: bool,
    webhook_include_backfill: bool | None = None,
    webhook_url: str | None,
    now: str | None = None,
    verify_webhook: bool = False,
) -> dict[str, Any]:
    repository = ApiTokenRepository(base_dir)
    old = repository.get_internal(token_id)
    if old is None:
        raise ApiTokenNotFoundError(token_id)
    if old.get("revoked_at") or old.get("replaced_by_token_id"):
        raise ApiTokenRevokedError(token_id)
    normalized_name = str(old["name"] if name is None else name).strip()
    if not normalized_name:
        raise ValueError("token name is required")
    normalized_scopes = list(old["scopes"]) if scopes is None else _validated_scopes(scopes)
    _validate_webhook_scope(normalized_scopes, webhook_enabled)
    normalized_url = _validated_webhook(webhook_enabled, webhook_url)
    include_backfill = (
        old["webhook_include_backfill"]
        if webhook_include_backfill is None else webhook_include_backfill
    )
    revision = int(old["config_revision"])
    webhook_changed = (
        old["webhook_enabled"] != bool(webhook_enabled)
        or str(old.get("webhook_url") or "") != str(normalized_url or "")
    )
    if verify_webhook and webhook_changed and webhook_enabled and normalized_url:
        _probe_or_raise(normalized_url, token_digest=str(old["token_digest"]))
    stored = repository.update_configuration(
        token_id,
        name=normalized_name,
        scopes=normalized_scopes,
        webhook_enabled=webhook_enabled,
        webhook_include_backfill=include_backfill,
        webhook_url=normalized_url,
        expected_revision=revision,
        reset_webhook_health=webhook_changed,
        delivery_status="active" if webhook_enabled and normalized_url else "inactive",
        updated_at=now,
    )
    if stored is None:
        raise ApiTokenConfigurationChangedError("Token configuration changed during save")
    return _public_token(stored)


def test_token_webhook(
    base_dir: Path | str,
    token_id: str,
    *,
    now: str | None = None,
) -> dict[str, Any]:
    repository = ApiTokenRepository(base_dir)
    token = repository.get_internal(token_id)
    if token is None:
        raise ApiTokenNotFoundError(token_id)
    if token.get("revoked_at") or token.get("replaced_by_token_id"):
        raise ApiTokenRevokedError(token_id)
    url = _validated_webhook(token["webhook_enabled"], token["webhook_url"])
    if not url:
        raise ValueError("webhook is not enabled")
    status_code = _probe_or_raise(url, token_digest=str(token["token_digest"]))
    updated = repository.record_webhook_success(
        token_id,
        attempted_at=now,
        expected_generation=token["webhook_config_generation"],
        allow_recovery=True,
    )
    if updated is None:
        raise WebhookConnectionError("Webhook configuration changed during the connection test")
    return {"ok": True, "status_code": status_code, "token": _public_token(updated)}


def replay_failed_webhooks(
    base_dir: Path | str,
    token_id: str,
    *,
    limit: int = 50,
    now: str | None = None,
) -> dict[str, Any]:
    from backend.infrastructure.storage.base import utc_now
    from backend.infrastructure.storage.webhook_replay import WebhookReplayStore

    result = WebhookReplayStore(base_dir).replay(
        token_id=token_id,
        scheduled_at=now or utc_now(),
        limit=limit,
    )
    if result["state"] == "missing":
        raise ApiTokenNotFoundError(token_id)
    if result["state"] == "revoked":
        raise ApiTokenRevokedError(token_id)
    if result["state"] != "active":
        raise ValueError("webhook is not active; test the connection before replay")
    replayed = result["task_ids"]
    return {"ok": True, "replayed_count": len(replayed), "task_ids": replayed}


def rotate_token(
    base_dir: Path | str,
    token_id: str,
    *,
    now: str | None = None,
    verify_webhook: bool = False,
) -> dict[str, Any]:
    repository = ApiTokenRepository(base_dir)
    old = repository.get_internal(token_id)
    if old is None:
        raise ApiTokenNotFoundError(token_id)
    if old.get("revoked_at"):
        raise ApiTokenRevokedError(token_id)

    material = _new_token_material()
    with repository.connect() as conn:
        new = ApiTokenRepository.create_to_connection(
            conn,
            token_id=material["token_id"],
            name=str(old["name"]),
            token_prefix=material["token_prefix"],
            token_digest=material["token_digest"],
            scopes=list(old["scopes"]),
            created_at=now,
            webhook_enabled=old["webhook_enabled"],
            webhook_include_backfill=old["webhook_include_backfill"],
            webhook_url=old.get("webhook_url"),
            webhook_initial_status=(
                "pending_verification"
                if verify_webhook and old["webhook_enabled"] and old["webhook_url"]
                else None
            ),
        )
        replaced = ApiTokenRepository.replace_to_connection(
            conn,
            token_id,
            replaced_by_token_id=str(new["token_id"]),
            revoked_at=now,
            expected_revision=old["config_revision"],
        )
        if replaced is None:
            raise ApiTokenConfigurationChangedError(
                "Token configuration changed during rotation"
            )
    return {
        "plaintext_token": material["plaintext_token"],
        "token": _public_token(new),
        "old_token": _public_token(replaced),
    }


def _new_token_material() -> dict[str, str]:
    plaintext = f"ios_{secrets.token_urlsafe(32)}"
    return {
        "token_id": str(uuid4()),
        "plaintext_token": plaintext,
        "token_prefix": f"{plaintext[:12]}…",
        "token_digest": sha256(plaintext.encode("utf-8")).hexdigest(),
    }


def _validated_scopes(scopes: list[str] | tuple[str, ...]) -> list[str]:
    normalized = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    if not normalized:
        raise ValueError("at least one token scope is required")
    unsupported = sorted(set(normalized) - ALLOWED_SCOPES)
    if unsupported:
        raise ValueError("unsupported token scope")
    return normalized


def _validated_webhook(enabled: bool, url: str | None) -> str | None:
    normalized = str(url or "").strip() or None
    if enabled and normalized is None:
        raise ValueError("webhook_url is required when webhook is enabled")
    return validate_webhook_url(normalized) if normalized is not None else None


def _validate_webhook_scope(scopes: list[str], enabled: bool) -> None:
    if enabled and "cards:read" not in scopes:
        raise ValueError("webhook delivery requires cards:read")


def _probe_or_raise(url: str, *, token_digest: str) -> int:
    try:
        status_code = probe_webhook(url, token_digest=token_digest, timeout_seconds=5)
    except WebhookTransportError as exc:
        raise WebhookConnectionError(f"Webhook connection test failed: {exc}") from exc
    if not 200 <= status_code < 300:
        raise WebhookConnectionError(
            f"Webhook connection test returned HTTP {status_code}"
        )
    return status_code


def _public_token(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "token_id",
            "name",
            "token_prefix",
            "scopes",
            "created_at",
            "revoked_at",
            "last_used_at",
            "replaced_by_token_id",
            "webhook_enabled",
            "webhook_include_backfill",
            "webhook_url",
            "webhook_config_generation",
            "webhook_delivery_status",
            "webhook_consecutive_failures",
            "webhook_last_attempt_at",
            "webhook_last_success_at",
            "webhook_last_failure_at",
            "webhook_last_error",
            "webhook_circuit_opened_at",
            "updated_at",
        )
    }
