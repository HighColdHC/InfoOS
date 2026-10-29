"""Deliver one metadata-only finalized-card notification."""
from __future__ import annotations

import hmac
import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.application.tasks.execution_clock import current_iso
from backend.application.tasks.types import Task, TaskStatus
from backend.infrastructure.storage.api_tokens import ApiTokenRepository
from backend.infrastructure.webhooks.delivery import WebhookTransportError, post_webhook


def handle_webhook_deliver(task: Task, base_dir: Path) -> dict[str, Any]:
    repository = ApiTokenRepository(base_dir)
    token_id = str(task.artifacts_summary.get("token_id") or "")
    expected_generation = int(
        task.artifacts_summary.get("webhook_config_generation") or 1
    )
    token = repository.get_internal(token_id)
    if (
        token is None
        or token.get("revoked_at")
        or token.get("replaced_by_token_id")
        or not token["webhook_enabled"]
        or not token.get("webhook_url")
    ):
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"token_id": token_id, "reason": "webhook_inactive"},
            "error": "webhook_inactive",
        }
    if token["webhook_config_generation"] != expected_generation:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"token_id": token_id, "reason": "webhook_config_changed"},
            "error": "webhook_config_changed",
        }
    if task.artifacts_summary.get("backfill") is True and not token["webhook_include_backfill"]:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"token_id": token_id, "reason": "backfill_disabled"},
            "error": "backfill_disabled",
        }
    if token["webhook_delivery_status"] == "paused":
        return {
            "status": TaskStatus.BLOCKED.value,
            "artifacts": {
                "token_id": token_id,
                "reason": "webhook_circuit_open",
                "consecutive_failures": token["webhook_consecutive_failures"],
            },
            "error": "webhook_circuit_open",
        }
    if token["webhook_delivery_status"] != "active":
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"token_id": token_id, "reason": "webhook_not_active"},
            "error": "webhook_not_active",
        }
    payload = _payload(task)
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    timestamp = current_iso()
    signature = hmac.new(bytes.fromhex(str(token["token_digest"])), body, sha256).hexdigest()
    try:
        status_code = post_webhook(
            str(token["webhook_url"]),
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-InfoOS-Event-Id": str(payload["event_id"]),
                "X-InfoOS-Timestamp": timestamp,
                "X-InfoOS-Signature": f"sha256={signature}",
            },
        )
    except WebhookTransportError as exc:
        state = repository.record_webhook_failure(
            token_id,
            error=str(exc),
            attempted_at=timestamp,
            expected_generation=expected_generation,
        )
        if state is None:
            return {
                "status": TaskStatus.SKIPPED.value,
                "artifacts": {"token_id": token_id, "reason": "webhook_config_changed"},
                "error": "webhook_config_changed",
            }
        if state["webhook_delivery_status"] == "paused":
            return {
                "status": TaskStatus.BLOCKED.value,
                "artifacts": {
                    "token_id": token_id,
                    "event_id": payload["event_id"],
                    "reason": "webhook_circuit_open",
                    "consecutive_failures": state["webhook_consecutive_failures"],
                },
                "error": "webhook_circuit_open",
            }
        raise
    artifacts = {"token_id": token_id, "event_id": payload["event_id"], "status_code": status_code}
    if 200 <= status_code < 300:
        state = repository.record_webhook_success(
            token_id,
            attempted_at=timestamp,
            expected_generation=expected_generation,
        )
        if state is None:
            return {
                "status": TaskStatus.SKIPPED.value,
                "artifacts": {"token_id": token_id, "reason": "webhook_config_changed"},
                "error": "webhook_config_changed",
            }
        return {"status": TaskStatus.SUCCESS.value, "artifacts": artifacts}
    state = repository.record_webhook_failure(
        token_id,
        error=f"webhook returned HTTP {status_code}",
        attempted_at=timestamp,
        expected_generation=expected_generation,
    )
    if state is None:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"token_id": token_id, "reason": "webhook_config_changed"},
            "error": "webhook_config_changed",
        }
    if state["webhook_delivery_status"] == "paused":
        return {
            "status": TaskStatus.BLOCKED.value,
            "artifacts": {
                **artifacts,
                "reason": "webhook_circuit_open",
                "consecutive_failures": state["webhook_consecutive_failures"],
            },
            "error": "webhook_circuit_open",
        }
    if status_code == 429 or status_code >= 500:
        raise WebhookTransportError(f"webhook returned HTTP {status_code}", status_code=status_code)
    if 300 <= status_code < 500:
        return {
            "status": TaskStatus.BLOCKED.value,
            "artifacts": artifacts,
            "error": f"webhook returned HTTP {status_code}",
        }
    raise WebhookTransportError(f"unexpected webhook HTTP {status_code}", status_code=status_code)


def _payload(task: Task) -> dict[str, Any]:
    artifacts = task.artifacts_summary
    payload = {
        "event_id": f"evt_{sha256(task.task_id.encode()).hexdigest()}",
        "event": "card.finalized",
        "card_id": task.target_id,
        "source_id": artifacts.get("source_id"),
        "card_version": int(artifacts["card_version"]),
        "content_hash": artifacts["content_hash"],
        "finalized_at": artifacts["finalized_at"],
        "pull_url": f"/api/plugin/v1/cards/{task.target_id}",
    }
    if "backfill" in artifacts:
        payload["backfill"] = artifacts["backfill"] is True
        if payload["backfill"]:
            payload["backfill_reason"] = artifacts.get("backfill_reason")
    return payload
