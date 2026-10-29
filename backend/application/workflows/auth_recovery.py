"""Manual authentication recovery workflow for browser-backed platforms."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import logging

from backend.application.setup.browser_session import launch_platform_login
from backend.application.setup.verify import confirmed_login_required, probe_platform
from backend.domain.sources.registry import platform_definition
from backend.application.usecases.notifications import enqueue_notification
from backend.domain.notifications import NotificationKind
from backend.infrastructure.notifications.config import load_config, readiness
from backend.infrastructure.runtime.environment import setup_state_path
from backend.infrastructure.runtime.setup_state import (
    AccountState,
    AccountStatus,
    update_account_state,
)
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.auth_recovery_reconciliation import (
    AuthRecoveryReconciliationRepository,
)
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.platform_fetch_recovery import prepare_platform_fetch_recovery

logger = logging.getLogger(__name__)


class AuthRecoveryNotFound(Exception):
    pass


class AuthRecoveryNotActionable(Exception):
    pass


class AuthRecoverySuperseded(Exception):
    pass


class AuthRecoveryRuntimeUnavailable(Exception):
    pass


class AuthRecoveryUnsupported(Exception):
    pass


_VERIFICATION_LEASE_SECONDS = 120
_NOTIFICATION_LEASE_SECONDS = 60


def record_auth_incident(
    base_dir: Path | str,
    *,
    circuit: dict[str, Any],
    task_id: str,
    occurred_at: str,
) -> dict[str, Any]:
    repo = AuthRecoveryRepository(base_dir)
    incident, _created = repo.create_or_link(
        platform=str(circuit["scope_key"]),
        outage_epoch=int(circuit["outage_epoch"]),
        reason_code=str(circuit["reason_code"]),
        task_id=task_id,
        occurred_at=occurred_at,
    )
    attempt_auth_notification(
        base_dir,
        str(incident["incident_id"]),
        attempted_at=occurred_at,
    )
    return repo.get(str(incident["incident_id"])) or incident


def complete_committed_auth_failure(
    base_dir: Path | str,
    *,
    incident_id: str,
    platform: str,
    occurred_at: str,
) -> None:
    """Run non-transactional user state and email work after task commit."""
    try:
        mark_account_needs_login(platform, checked_at=occurred_at)
    except Exception:
        logger.exception("auth_recovery_account_mark_failed incident_id=%s platform=%s", incident_id, platform)
    try:
        attempt_auth_notification(base_dir, incident_id, attempted_at=occurred_at)
    except Exception:
        logger.exception("auth_recovery_notification_failed incident_id=%s platform=%s", incident_id, platform)


def attempt_auth_notification(
    base_dir: Path | str,
    incident_id: str,
    *,
    attempted_at: str,
) -> str:
    repo = AuthRecoveryRepository(base_dir)
    incident = repo.get(incident_id)
    if incident is None:
        return "missing"
    if NotificationOutboxRepository(base_dir).incident_has_delivery(incident_id):
        return incident['notification_status']
    claim_token = repo.claim_notification(
        incident_id,
        lease_until=_add_seconds(attempted_at, _NOTIFICATION_LEASE_SECONDS),
        updated_at=attempted_at,
    )
    if claim_token is None:
        return str(incident["notification_status"])
    from backend.application.workflows.auth_qr import start_qr_round, QrRoundUnavailable

    definition = platform_definition(incident["platform"])
    if definition is None:
        notification_status = "skipped"
    elif definition.qr_login is not None and readiness(load_config())["feishu"]:
        try:
            start_qr_round(base_dir, incident_id, now=attempted_at)
        except QrRoundUnavailable:
            pass  # Incident can be concurrently resolved; no notification is needed.
        notification_status = "pending"
    else:
        ids = enqueue_notification(base_dir, kind=NotificationKind.AUTH_RECOVERY_TEXT,
            title=f"{_platform_label(incident['platform'])} 需要重新登录",
            body="请在局域网 Web 的采集环境页打开 Browser Console，完成登录并验证。",
            dedupe_key=f"auth-recovery:{incident_id}", related=("auth_incident", incident_id), now=attempted_at)
        notification_status = "pending" if ids else "skipped"
    repo.complete_notification(incident_id, claim_token=claim_token, status=notification_status,
                               notified_at=None, updated_at=attempted_at)
    return notification_status


def drain_auth_recovery_notifications(
    base_dir: Path | str,
    *,
    now: str | None = None,
    limit: int = 10,
) -> dict[str, int]:
    attempted_at = now or utc_now_seconds_z()
    repo = AuthRecoveryRepository(base_dir)
    reconciled = AuthRecoveryReconciliationRepository(
        base_dir
    ).reconcile_manual_login_circuits(
        updated_at=attempted_at,
    )
    for incident in reconciled:
        try:
            mark_account_needs_login(
                str(incident["platform"]),
                checked_at=attempted_at,
            )
        except Exception:
            logger.exception("auth_recovery_reconcile_mark_failed incident_id=%s platform=%s", incident.get("incident_id"), incident.get("platform"))
    if any(readiness(load_config()).values()):
        repo.requeue_skipped_notifications(updated_at=attempted_at)
    candidates = repo.list_notification_candidates(now=attempted_at, limit=limit)
    counts = {"claimed": 0, "sent": 0, "pending": 0, "failed": 0, "skipped": 0}
    for incident_id in candidates:
        if NotificationOutboxRepository(base_dir).incident_has_delivery(incident_id):
            continue
        outcome = attempt_auth_notification(
            base_dir,
            incident_id,
            attempted_at=attempted_at,
        )
        if outcome in {"sent", "pending", "failed", "skipped"}:
            counts["claimed"] += 1
            counts[outcome] += 1
    return counts


def mark_account_needs_login(platform: str, *, checked_at: str) -> AccountState:
    return update_account_state(
        setup_state_path(),
        platform,
        lambda previous: AccountState(
            status=AccountStatus.NEEDS_LOGIN,
            last_checked=checked_at,
            error=None,
            persisted=False,
            persistence_checked_at=None,
            persistence_stage=None,
            persistence_error=None,
            raw=dict(previous.raw),
        ),
    )


def get_auth_incident(base_dir: Path | str, incident_id: str) -> dict[str, Any]:
    incident = AuthRecoveryRepository(base_dir).get(incident_id)
    if incident is None:
        raise AuthRecoveryNotFound
    return _project_incident(incident)


def open_auth_console(
    base_dir: Path | str,
    incident_id: str,
) -> tuple[str, dict[str, Any]]:
    incident = AuthRecoveryRepository(base_dir).get(incident_id)
    if incident is None:
        raise AuthRecoveryNotFound
    if incident["status"] == "resolved":
        raise AuthRecoveryNotActionable
    try:
        result = launch_platform_login(str(incident["platform"]))
    except (OSError, RuntimeError) as exc:
        raise AuthRecoveryRuntimeUnavailable from exc
    except ValueError as exc:
        raise AuthRecoveryUnsupported from exc
    if not result.get("ok"):
        raise AuthRecoveryRuntimeUnavailable
    return str(result["console_url"]), _project_incident(incident)


def verify_auth_recovery(
    base_dir: Path | str,
    incident_id: str,
    *,
    checked_at: str | None = None,
) -> dict[str, Any]:
    now = checked_at or utc_now_seconds_z()
    repo = AuthRecoveryRepository(base_dir)
    existing = repo.get(incident_id)
    if existing is None:
        raise AuthRecoveryNotFound
    if existing["status"] == "resolved":
        raise AuthRecoveryNotActionable
    incident = repo.begin_verification(
        incident_id,
        updated_at=now,
        stale_before=_add_seconds(now, -_VERIFICATION_LEASE_SECONDS),
    )
    if incident is None:
        raise AuthRecoverySuperseded
    claim_token = str(incident["verification_claim_token"])
    try:
        account = probe_platform(str(incident["platform"]))
    except Exception as exc:
        _reset_after_runtime_failure(
            repo,
            incident_id,
            platform=str(incident["platform"]),
            claim_token=claim_token,
            updated_at=now,
        )
        raise AuthRecoveryRuntimeUnavailable from exc
    if account.status is not AccountStatus.VERIFIED:
        failed = repo.verification_failed(
            incident_id,
            claim_token=claim_token,
            error_code="platform_session_not_ready",
            updated_at=now,
        )
        if failed is None:
            raise AuthRecoverySuperseded
        return {
            "ok": False,
            "verification": {
                "status": "failed",
                "code": "platform_session_not_ready",
                "login_required": confirmed_login_required(account),
                "checked_at": now,
            },
            "incident": _project_incident(failed),
        }
    try:
        outcome, resolved = resolve_verified_auth_recovery(
            repo,
            incident_id,
            claim_token=claim_token,
            platform=str(incident["platform"]),
            outage_epoch=int(incident["outage_epoch"]),
            occurred_at=now,
        )
    except Exception as exc:
        _reset_after_runtime_failure(
            repo,
            incident_id,
            platform=str(incident["platform"]),
            claim_token=claim_token,
            updated_at=now,
        )
        raise AuthRecoveryRuntimeUnavailable from exc
    if outcome == "tasks_not_ready" and resolved is not None:
        return {
            "ok": False,
            "verification": {
                "status": "failed",
                "code": "recovery_tasks_not_ready",
                "login_required": False,
                "checked_at": now,
            },
            "incident": _project_incident(resolved),
        }
    if outcome != "resolved" or resolved is None:
        _reset_after_runtime_failure(
            repo,
            incident_id,
            platform=str(incident["platform"]),
            claim_token=claim_token,
            updated_at=now,
            error_code="recovery_superseded",
        )
        raise AuthRecoverySuperseded
    try:
        _persist_verified_account(str(incident["platform"]), account, checked_at=now)
    except Exception:
        logger.exception("auth_recovery_verified_account_persist_failed incident_id=%s platform=%s", incident_id, incident.get("platform"))
    from backend.application.workflows.auth_qr import finish_resolved_incident
    finish_resolved_incident(base_dir, resolved, now=now)
    return {
        "ok": True,
        "verification": {
            "status": "passed",
            "checked_at": now,
            "method": "platform_functional_probe",
        },
        "incident": _project_incident(resolved),
    }


def resolve_verified_auth_recovery(
    repo: AuthRecoveryRepository,
    incident_id: str,
    *,
    claim_token: str,
    platform: str,
    outage_epoch: int,
    occurred_at: str,
) -> tuple[str, dict[str, Any] | None]:
    """Own the recovery transaction while storage exposes focused SQL actions."""
    conn = repo.open_workflow_connection()
    try:
        conn.execute("begin immediate")
        if not repo.verification_claim_matches_to_connection(
            conn,
            incident_id=incident_id,
            claim_token=claim_token,
            platform=platform,
            outage_epoch=outage_epoch,
        ):
            conn.execute("rollback")
            return "superseded", None
        total, ready = repo.linked_task_readiness_to_connection(conn, incident_id)
        if total == 0 or ready != total:
            updated = repo.reset_tasks_not_ready_to_connection(
                conn,
                incident_id=incident_id,
                claim_token=claim_token,
                updated_at=occurred_at,
            )
            if not updated:
                conn.execute("rollback")
                return "superseded", None
            conn.execute("commit")
            return "tasks_not_ready", repo.get(incident_id)
        if not repo.close_manual_circuit_to_connection(
            conn,
            platform=platform,
            outage_epoch=outage_epoch,
            updated_at=occurred_at,
        ):
            conn.execute("rollback")
            return "superseded", None
        TaskEventRepository.append_to_connection(
            conn,
            event_type="circuit_closed",
            payload={
                "version": 1,
                "incident_id": incident_id,
                "scope_kind": "platform",
                "scope_key": platform,
                "platform": platform,
                "outage_epoch": outage_epoch,
            },
            occurred_at=occurred_at,
        )
        recovered_count = 0
        for task_row in prepare_platform_fetch_recovery(
            conn, platform, occurred_at=occurred_at, incident_id=incident_id,
        ):
            task_id = str(task_row["task_id"])
            if not repo.requeue_task_to_connection(
                conn,
                task_id=task_id,
                updated_at=occurred_at,
                next_run_at=task_row["next_run_at"],
            ):
                continue
            recovered_count += 1
            TaskEventRepository.append_to_connection(
                conn,
                event_type="auth_recovery_scheduled",
                task_id=task_id,
                chain_id=task_row.get("chain_id"),
                status="pending",
                failure_class="platform_not_logged_in",
                payload={
                    "version": 1,
                    "incident_id": incident_id,
                    "platform": platform,
                    "outage_epoch": outage_epoch,
                },
                occurred_at=occurred_at,
            )
        if not repo.resolve_incident_to_connection(
            conn,
            incident_id=incident_id,
            claim_token=claim_token,
            platform=platform,
            outage_epoch=outage_epoch,
            recovered_task_count=recovered_count,
            updated_at=occurred_at,
        ):
            conn.execute("rollback")
            return "superseded", None
        conn.execute("commit")
    except Exception:
        if conn.in_transaction:
            conn.execute("rollback")
        raise
    finally:
        conn.close()
    return "resolved", repo.get(incident_id)


def _persist_verified_account(
    platform: str,
    account: AccountState,
    *,
    checked_at: str,
) -> None:
    update_account_state(
        setup_state_path(),
        platform,
        lambda previous: AccountState(
            status=AccountStatus.VERIFIED,
            last_checked=account.last_checked or checked_at,
            error=None,
            persisted=False,
            persistence_checked_at=None,
            persistence_stage=None,
            persistence_error=None,
            raw=dict(previous.raw),
        ),
    )


def _reset_after_runtime_failure(
    repo: AuthRecoveryRepository,
    incident_id: str,
    *,
    platform: str,
    claim_token: str,
    updated_at: str,
    error_code: str = "recovery_verification_unavailable",
) -> None:
    try:
        repo.verification_failed(
            incident_id,
            claim_token=claim_token,
            error_code=error_code,
            updated_at=updated_at,
        )
    except Exception:
        logger.exception("auth_recovery_reset_failed incident_id=%s platform=%s", incident_id, platform)


def _project_incident(incident: dict[str, Any]) -> dict[str, Any]:
    return {
        "incident_id": incident["incident_id"],
        "platform": incident["platform"],
        "platform_label": _platform_label(str(incident["platform"])),
        "status": incident["status"],
        "reason_code": incident["reason_code"],
        "created_at": incident["created_at"],
        "updated_at": incident["updated_at"],
        "resolved_at": incident["resolved_at"],
        "affected_task_count": incident["affected_task_count"],
        "recovered_task_count": incident["recovered_task_count"],
        "last_error_code": incident.get("last_error_code"),
    }


def _platform_label(platform: str) -> str:
    definition = platform_definition(platform)
    return definition.label if definition is not None else platform




def _add_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
