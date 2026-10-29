"""Platform circuit effects for explicit acquisition failures."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import os
import sqlite3
from typing import Any, Callable

from backend.application.tasks.types import Task
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.application.workflows.auth_recovery import (
    complete_committed_auth_failure,
)
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository


MANUAL_PLATFORM_FAILURES = {
    FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
    FetchFailureReason.CHROME_NOT_CONFIGURED,
}


def is_platform_upstream_failure(error: BaseException | str) -> bool:
    from backend.application.workflows.acquisition_failures import (
        AcquisitionFailureError,
    )

    return isinstance(error, AcquisitionFailureError)


def zsxq_cooldown_retry_at(base_path: Path, *, owner_token: str | None, now: str) -> str | None:
    return platform_cooldown_retry_at(base_path, platform_scope="zsxq", owner_token=owner_token, now=now)


def platform_cooldown_retry_at(
    base_path: Path, *, platform_scope: str, owner_token: str | None, now: str,
) -> str | None:
    """Recheck tasks admitted in the same snapshot before a preceding rate limit."""
    row = RuntimeCircuitRepository(base_path).get("platform", platform_scope)
    if not row or row["reason_code"] != FetchFailureReason.RATE_LIMITED.value:
        return None
    if row["state"] == "open":
        # An expired circuit still needs fresh admission to acquire the single
        # half-open slot. A task from an older snapshot must return to pending.
        return max(str(row["cooldown_until"] or now), now)
    if row["state"] == "half_open" and row["probe_owner_token"] != owner_token:
        return max(str(row["probe_lease_expires_at"] or now), now)
    return None


def release_source_unavailable_probe(
    base_path: Path, *, platform_scope: str | None, owner_token: str | None,
    reason: FetchFailureReason, occurred_at: str,
) -> None:
    """A source lookup failure provides no evidence about platform health."""
    if reason == FetchFailureReason.SOURCE_UNAVAILABLE and platform_scope and owner_token:
        RuntimeCircuitRepository(base_path).release_probe(
            "platform", platform_scope, owner_token=owner_token, now=occurred_at,
        )


def record_platform_failure(
    base_path: Path,
    task: Task,
    *,
    platform_scope: str,
    owner_token: str | None,
    reason: FetchFailureReason,
    occurred_at: str,
) -> None:
    """Record an automatic circuit effect after task persistence succeeds."""
    if reason in MANUAL_PLATFORM_FAILURES:
        raise ValueError("manual platform failures require the task transaction")
    circuits = RuntimeCircuitRepository(base_path)
    event_context = {
        "task_id": task.task_id,
        "chain_id": task.artifacts_summary.get("chain_id"),
    }
    if reason in {
        FetchFailureReason.RATE_LIMITED,
        FetchFailureReason.NETWORK_TIMEOUT,
        FetchFailureReason.OPENCLI_COMMAND_FAILED,
        FetchFailureReason.OPENCLI_DAEMON_DOWN,
        FetchFailureReason.BROWSER_PAGE_LOAD_FAILED,
        FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED,
        FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED,
    }:
        policy = None
        if platform_scope in {"zsxq", "x"} and reason == FetchFailureReason.RATE_LIMITED:
            default = 600 if platform_scope == "zsxq" else 1800
            try:
                cooldown = int(os.environ.get(f"INFOOS_{platform_scope.upper()}_CIRCUIT_COOLDOWN_SECONDS", str(default)))
            except ValueError:
                cooldown = default
            if platform_scope == "zsxq":
                cooldown = max(1, cooldown)
            elif cooldown <= 0:
                cooldown = default
            policy = replace(CircuitPolicy.from_environment("platform"),
                             failure_threshold=1, cooldown_seconds=cooldown)
        circuits.record_failure(
            "platform",
            platform_scope,
            occurred_at=occurred_at,
            owner_token=owner_token,
            reason_code=reason.value,
            policy=policy,
            **event_context,
        )


def record_manual_platform_failure_to_connection(
    conn: sqlite3.Connection,
    task: Task,
    *,
    platform_scope: str,
    reason: FetchFailureReason,
    occurred_at: str,
) -> dict[str, Any]:
    if reason not in MANUAL_PLATFORM_FAILURES:
        raise ValueError("automatic platform failure cannot use manual transaction")
    return AuthRecoveryRepository.open_manual_and_link_to_connection(
        conn,
        platform=platform_scope,
        reason_code=reason.value,
        task_id=task.task_id,
        chain_id=task.artifacts_summary.get("chain_id"),
        occurred_at=occurred_at,
    )


def manual_platform_failure_callback(
    effect: dict[str, Any],
    task: Task,
    *,
    platform_scope: str | None,
    reason: FetchFailureReason,
    occurred_at: str,
) -> Callable[[sqlite3.Connection], None] | None:
    if not str(platform_scope or "").strip():
        raise ValueError("manual platform failure requires a platform scope")
    if reason not in MANUAL_PLATFORM_FAILURES:
        return None

    def callback(conn: sqlite3.Connection) -> None:
        effect.update(
            record_manual_platform_failure_to_connection(
                conn,
                task,
                platform_scope=str(platform_scope),
                reason=reason,
                occurred_at=occurred_at,
            )
        )

    return callback


def complete_manual_platform_failure(
    base_path: Path,
    effect: dict[str, Any],
    *,
    occurred_at: str,
) -> None:
    incident_id = effect.get("incident_id")
    if not incident_id:
        return
    complete_committed_auth_failure(
        base_path,
        incident_id=str(incident_id),
        platform=str(effect["platform"]),
        occurred_at=occurred_at,
    )
