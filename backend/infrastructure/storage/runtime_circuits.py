"""Atomic, passive-safe platform/provider circuit state."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
from typing import Any, cast

from backend.domain.failures.facts import safe_identifier
from backend.domain.sources.registry import PLATFORM_REGISTRY
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.runtime_circuits_types import CircuitRow, SafeCircuitRow
from backend.infrastructure.storage.task_events import TaskEventRepository


@dataclass(frozen=True)
class CircuitPolicy:
    failure_threshold: int = 3
    cooldown_seconds: int = 900
    probe_lease_seconds: int = 60

    @classmethod
    def from_environment(cls, scope_kind: str) -> "CircuitPolicy":
        prefix = f"INFOOS_{scope_kind.upper()}_CIRCUIT"
        return cls(
            failure_threshold=max(1, _env_int(f"{prefix}_FAILURE_THRESHOLD", 3)),
            cooldown_seconds=max(1, _env_int(f"{prefix}_COOLDOWN_SECONDS", 900)),
            probe_lease_seconds=max(
                1, _env_int(f"{prefix}_PROBE_LEASE_SECONDS", 300)
            ),
        )


@dataclass(frozen=True)
class CircuitAdmission:
    allowed: bool
    state: str
    probe: bool = False
    retry_at: str | None = None


class RuntimeCircuitRepository:
    def __init__(self, base_dir: str | Path):
        self.db_path = resolve_sqlite_path(base_dir)

    def admit(
        self,
        scope_kind: str,
        scope_key: str,
        *,
        owner_token: str,
        now: str,
        policy: CircuitPolicy | None = None,
        task_id: str | None = None,
        chain_id: str | None = None,
    ) -> CircuitAdmission:
        _validate_scope(scope_kind, scope_key)
        policy = policy or CircuitPolicy.from_environment(scope_kind)
        with closing(self._connection()) as read_conn:
            observed = read_conn.execute(
                "select state from runtime_circuits where scope_kind = ? and scope_key = ?",
                (scope_kind, scope_key),
            ).fetchone()
        if observed is None or observed["state"] == "closed":
            return CircuitAdmission(True, "closed")
        conn = self._connection()
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from runtime_circuits where scope_kind = ? and scope_key = ?",
                (scope_kind, scope_key),
            ).fetchone()
            if row is None or row["state"] == "closed":
                conn.execute("commit")
                return CircuitAdmission(True, "closed")
            if row["recovery_mode"] == "manual":
                conn.execute("commit")
                return CircuitAdmission(False, "open")
            if row["state"] == "half_open":
                if str(row["probe_lease_expires_at"] or "") > now:
                    conn.execute("commit")
                    return CircuitAdmission(
                        False,
                        "half_open",
                        retry_at=row["probe_lease_expires_at"],
                    )
                cursor = conn.execute(
                    """
                    update runtime_circuits
                    set probe_owner_token = ?, probe_acquired_at = ?,
                        probe_lease_expires_at = ?, updated_at = ?
                    where scope_kind = ? and scope_key = ? and state = 'half_open'
                      and (probe_lease_expires_at is null or probe_lease_expires_at <= ?)
                    """,
                    (
                        owner_token,
                        now,
                        _add_seconds(now, policy.probe_lease_seconds),
                        now,
                        scope_kind,
                        scope_key,
                        now,
                    ),
                )
                conn.execute("commit")
                return CircuitAdmission(
                    bool(cursor.rowcount),
                    "half_open",
                    probe=bool(cursor.rowcount),
                )
            if str(row["cooldown_until"] or "") > now:
                conn.execute("commit")
                return CircuitAdmission(False, "open", retry_at=row["cooldown_until"])
            cursor = conn.execute(
                """
                update runtime_circuits
                set state = 'half_open', probe_owner_token = ?,
                    probe_acquired_at = ?, probe_lease_expires_at = ?, updated_at = ?
                where scope_kind = ? and scope_key = ? and state = 'open'
                  and cooldown_until <= ?
                """,
                (
                    owner_token,
                    now,
                    _add_seconds(now, policy.probe_lease_seconds),
                    now,
                    scope_kind,
                    scope_key,
                    now,
                ),
            )
            if cursor.rowcount:
                _append_transition(
                    conn,
                    "circuit_half_opened",
                    scope_kind,
                    scope_key,
                    occurred_at=now,
                    task_id=task_id,
                    chain_id=chain_id,
                )
                conn.execute("commit")
                return CircuitAdmission(True, "half_open", probe=True)
            conn.execute("commit")
            return CircuitAdmission(False, "half_open")
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def release_probe(
        self, scope_kind: str, scope_key: str, *, owner_token: str, now: str,
    ) -> bool:
        _validate_scope(scope_kind, scope_key)
        conn = self._connection()
        try:
            cursor = conn.execute(
                """update runtime_circuits
                   set probe_owner_token = null, probe_acquired_at = null,
                       probe_lease_expires_at = null, updated_at = ?
                   where scope_kind = ? and scope_key = ? and state = 'half_open'
                     and probe_owner_token = ?""",
                (now, scope_kind, scope_key, owner_token),
            )
            return bool(cursor.rowcount)
        finally:
            conn.close()

    def renew_probe(
        self, scope_kind: str, scope_key: str, *, owner_token: str,
        now: str, lease_seconds: int,
    ) -> bool:
        """Keep an owned probe alive; write only below half its lease lifetime."""
        _validate_scope(scope_kind, scope_key)
        duration = max(1, int(lease_seconds))
        threshold = _add_seconds(now, (duration + 1) // 2)
        with closing(self._connection()) as read_conn:
            row = read_conn.execute(
                """select probe_lease_expires_at from runtime_circuits
                   where scope_kind = ? and scope_key = ? and state = 'half_open'
                     and probe_owner_token = ?""",
                (scope_kind, scope_key, owner_token),
            ).fetchone()
        if row is None:
            return False
        if str(row["probe_lease_expires_at"] or "") >= threshold:
            return True
        conn = self._connection()
        try:
            cursor = conn.execute(
                """update runtime_circuits
                   set probe_lease_expires_at = ?, updated_at = ?
                   where scope_kind = ? and scope_key = ? and state = 'half_open'
                     and probe_owner_token = ?
                     and (probe_lease_expires_at is null or probe_lease_expires_at < ?)""",
                (_add_seconds(now, duration), now, scope_kind, scope_key,
                 owner_token, threshold),
            )
            if cursor.rowcount:
                return True
        finally:
            conn.close()
        with closing(self._connection()) as read_conn:
            return read_conn.execute(
                """select 1 from runtime_circuits where scope_kind = ? and scope_key = ?
                   and state = 'half_open' and probe_owner_token = ?""",
                (scope_kind, scope_key, owner_token),
            ).fetchone() is not None

    def record_failure(
        self,
        scope_kind: str,
        scope_key: str,
        *,
        occurred_at: str,
        retry_after_seconds: int | None = None,
        owner_token: str | None = None,
        reason_code: str = "upstream_unavailable",
        policy: CircuitPolicy | None = None,
        task_id: str | None = None,
        chain_id: str | None = None,
    ) -> str:
        _validate_scope(scope_kind, scope_key)
        safe_identifier(reason_code, field="reason_code")
        policy = policy or CircuitPolicy.from_environment(scope_kind)
        cooldown = max(policy.cooldown_seconds, int(retry_after_seconds or 0))
        cooldown_until = _add_seconds(occurred_at, cooldown)
        conn = self._connection()
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from runtime_circuits where scope_kind = ? and scope_key = ?",
                (scope_kind, scope_key),
            ).fetchone()
            if row is not None and row["recovery_mode"] == "manual":
                conn.execute("commit")
                return "open"
            failures = int(row["consecutive_failures"] if row else 0) + 1
            was_probe = bool(
                row
                and row["state"] == "half_open"
                and (owner_token is None or row["probe_owner_token"] == owner_token)
            )
            state = (
                "open"
                if failures >= policy.failure_threshold or was_probe
                else "closed"
            )
            opened_at = (
                occurred_at
                if state == "open" and (row is None or row["state"] != "open")
                else (row["opened_at"] if row else None)
            )
            outage_epoch = int(row["outage_epoch"] if row else 0)
            if (
                state == "open"
                and (row is None or row["state"] == "closed")
            ):
                outage_epoch += 1
            conn.execute(
                """
                insert into runtime_circuits (
                  scope_kind, scope_key, state, recovery_mode,
                  outage_epoch, consecutive_failures, opened_at,
                  cooldown_until, last_failure_at, last_success_at,
                  reason_code, user_action, probe_owner_token,
                  probe_acquired_at, probe_lease_expires_at, updated_at
                ) values (?, ?, ?, 'automatic', ?, ?, ?, ?, ?, null, ?, ?, null, null, null, ?)
                on conflict(scope_kind, scope_key) do update set
                  state = excluded.state,
                  recovery_mode = 'automatic',
                  outage_epoch = excluded.outage_epoch,
                  consecutive_failures = excluded.consecutive_failures,
                  opened_at = excluded.opened_at,
                  cooldown_until = excluded.cooldown_until,
                  last_failure_at = excluded.last_failure_at,
                  reason_code = excluded.reason_code,
                  user_action = excluded.user_action,
                  probe_owner_token = null,
                  probe_acquired_at = null,
                  probe_lease_expires_at = null,
                  updated_at = excluded.updated_at
                """,
                (
                    scope_kind,
                    scope_key,
                    state,
                    outage_epoch,
                    failures,
                    opened_at,
                    cooldown_until if state == "open" else None,
                    occurred_at,
                    reason_code,
                    "automatic_retry_after_cooldown",
                    occurred_at,
                ),
            )
            if state == "open" and (row is None or row["state"] != "open"):
                _append_transition(
                    conn,
                    "circuit_opened",
                    scope_kind,
                    scope_key,
                    occurred_at=occurred_at,
                    task_id=task_id,
                    chain_id=chain_id,
                    reason_code=reason_code,
                )
            conn.execute("commit")
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()
        return state

    def open_manual(
        self,
        scope_kind: str,
        scope_key: str,
        *,
        occurred_at: str,
        reason_code: str,
        user_action: str,
        task_id: str | None = None,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_scope(scope_kind, scope_key)
        safe_identifier(reason_code, field="reason_code")
        safe_identifier(user_action, field="user_action")
        conn = self._connection()
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select state, outage_epoch from runtime_circuits where scope_kind = ? and scope_key = ?",
                (scope_kind, scope_key),
            ).fetchone()
            outage_epoch = int(row["outage_epoch"] if row else 0)
            if row is None or row["state"] == "closed":
                outage_epoch += 1
            conn.execute(
                """
                insert into runtime_circuits (
                  scope_kind, scope_key, state, recovery_mode,
                  outage_epoch, consecutive_failures, opened_at, cooldown_until,
                  last_failure_at, reason_code, user_action, updated_at
                ) values (?, ?, 'open', 'manual', ?, 1, ?, null, ?, ?, ?, ?)
                on conflict(scope_kind, scope_key) do update set
                  state = 'open', recovery_mode = 'manual',
                  outage_epoch = excluded.outage_epoch,
                  consecutive_failures = runtime_circuits.consecutive_failures + 1,
                  opened_at = coalesce(runtime_circuits.opened_at, excluded.opened_at),
                  cooldown_until = null, last_failure_at = excluded.last_failure_at,
                  reason_code = excluded.reason_code, user_action = excluded.user_action,
                  probe_owner_token = null, probe_acquired_at = null,
                  probe_lease_expires_at = null, updated_at = excluded.updated_at
                """,
                (
                    scope_kind,
                    scope_key,
                    outage_epoch,
                    occurred_at,
                    occurred_at,
                    reason_code,
                    user_action,
                    occurred_at,
                ),
            )
            if row is None or row["state"] != "open":
                _append_transition(
                    conn,
                    "circuit_opened",
                    scope_kind,
                    scope_key,
                    occurred_at=occurred_at,
                    task_id=task_id,
                    chain_id=chain_id,
                    reason_code=reason_code,
                )
            snapshot_row = conn.execute(
                """
                select scope_kind, scope_key, state, recovery_mode, outage_epoch,
                       opened_at, reason_code, user_action, updated_at
                from runtime_circuits
                where scope_kind = ? and scope_key = ?
                """,
                (scope_kind, scope_key),
            ).fetchone()
            conn.execute("commit")
            return dict(snapshot_row)
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def record_success(
        self,
        scope_kind: str,
        scope_key: str,
        *,
        occurred_at: str,
        owner_token: str | None = None,
        explicit_recovery: bool = False,
        task_id: str | None = None,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_scope(scope_kind, scope_key)
        conn = self._connection()
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                """
                select state, recovery_mode, outage_epoch
                from runtime_circuits where scope_kind = ? and scope_key = ?
                """,
                (scope_kind, scope_key),
            ).fetchone()
            if row is not None and row["recovery_mode"] == "manual" and not explicit_recovery:
                conn.execute("commit")
                return {
                    "closed": False,
                    "transitioned": False,
                    "outage_epoch": row["outage_epoch"],
                }
            cursor = conn.execute(
                """
                insert into runtime_circuits (
                  scope_kind, scope_key, state, consecutive_failures,
                  last_success_at, updated_at
                ) values (?, ?, 'closed', 0, ?, ?)
                on conflict(scope_kind, scope_key) do update set
                  state = 'closed', consecutive_failures = 0, opened_at = null,
                  recovery_mode = 'automatic',
                  cooldown_until = null, last_success_at = excluded.last_success_at,
                  reason_code = null, user_action = null,
                  probe_owner_token = null, probe_acquired_at = null,
                  probe_lease_expires_at = null,
                  updated_at = excluded.updated_at
                where runtime_circuits.state != 'half_open'
                   or runtime_circuits.probe_owner_token = ?
                """,
                (scope_kind, scope_key, occurred_at, occurred_at, owner_token),
            )
            if cursor.rowcount and row is not None and row["state"] != "closed":
                if scope_kind == "platform":
                    from backend.infrastructure.storage.platform_fetch_recovery import prepare_platform_fetch_recovery
                    prepare_platform_fetch_recovery(conn, scope_key, occurred_at=occurred_at)
                _append_transition(
                    conn,
                    "circuit_closed",
                    scope_kind,
                    scope_key,
                    occurred_at=occurred_at,
                    task_id=task_id,
                    chain_id=chain_id,
                )
            conn.execute("commit")
            return {
                "closed": bool(cursor.rowcount),
                "transitioned": bool(
                    cursor.rowcount and row is not None and row["state"] != "closed"
                ),
                "outage_epoch": int(row["outage_epoch"] if row else 0),
            }
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def get(self, scope_kind: str, scope_key: str) -> CircuitRow | None:
        _validate_scope(scope_kind, scope_key)
        with self._connection() as conn:
            row = conn.execute(
                "select * from runtime_circuits where scope_kind = ? and scope_key = ?",
                (scope_kind, scope_key),
            ).fetchone()
        return cast(CircuitRow, dict(row)) if row else None

    def list_safe(self) -> list[SafeCircuitRow]:
        platforms = tuple(PLATFORM_REGISTRY)
        placeholders = ",".join("?" for _ in platforms)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                select scope_kind, scope_key, state, consecutive_failures,
                       outage_epoch,
                       opened_at, cooldown_until as next_probe_at,
                       reason_code, last_failure_at, last_success_at,
                       case
                         when recovery_mode != 'manual' then 'none'
                         when reason_code = 'platform_not_logged_in' then 'login_required'
                         else 'config_required'
                       end as user_action,
                       updated_at
                from runtime_circuits
                where state != 'closed' and scope_kind = 'platform'
                  and (scope_key in ({placeholders}) or scope_key like 'rss:%')
                order by scope_kind, scope_key
                """,
                platforms,
            ).fetchall()
        return [cast(SafeCircuitRow, dict(row)) for row in rows]

    def list_closed_provider_outages(self) -> list[dict[str, Any]]:
        """Return only safe identities needed by reconciliation recovery."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                select scope_key, outage_epoch
                from runtime_circuits
                where scope_kind = 'provider' and state = 'closed'
                  and recovery_mode = 'automatic' and outage_epoch >= 0
                order by scope_key
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_open_provider_outages(self) -> list[dict[str, Any]]:
        """Return automatic open providers eligible for one durable probe."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                select scope_key, outage_epoch, cooldown_until
                from runtime_circuits
                where scope_kind = 'provider' and state = 'open'
                  and recovery_mode = 'automatic' and outage_epoch >= 0
                  and cooldown_until is not null
                order by scope_key
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 30000")
        return conn


def _validate_scope(scope_kind: str, scope_key: str) -> None:
    if scope_kind not in {"platform", "provider"}:
        raise ValueError("invalid circuit scope kind")
    safe_identifier(scope_key, field="scope_key")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _add_seconds(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _append_transition(
    conn: sqlite3.Connection,
    event_type: str,
    scope_kind: str,
    scope_key: str,
    *,
    occurred_at: str,
    task_id: str | None,
    chain_id: str | None,
    reason_code: str | None = None,
) -> None:
    if task_id is None:
        return
    payload = {
        "version": 1,
        "scope_kind": scope_kind,
        "scope_key": scope_key,
    }
    if reason_code is not None:
        payload["reason_code"] = reason_code
    TaskEventRepository.append_to_connection(
        conn,
        event_type=event_type,
        task_id=task_id,
        chain_id=chain_id,
        payload=payload,
        occurred_at=occurred_at,
    )
