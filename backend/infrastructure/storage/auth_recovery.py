"""SQLite repository for authentication recovery incidents."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from backend.infrastructure.storage.auth_recovery_types import ActionableAuthRecoveryRow
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


class AuthRecoveryRepository(InfoOSRepository):
    def __init__(self, base_dir: Path | str):
        super().__init__(base_dir)

    def create_or_link(
        self,
        *,
        platform: str,
        outage_epoch: int,
        reason_code: str,
        task_id: str,
        occurred_at: str,
    ) -> tuple[dict[str, Any], bool]:
        conn = self._write_connection()
        try:
            conn.execute("begin immediate")
            incident_id = f"authrec_{uuid4().hex}"
            cursor = conn.execute(
                """
                insert or ignore into auth_recovery_incidents (
                  incident_id, platform, outage_epoch, reason_code, status,
                  created_at, updated_at, notification_status
                ) values (?, ?, ?, ?, 'awaiting_user', ?, ?, 'pending')
                """,
                (
                    incident_id,
                    platform,
                    outage_epoch,
                    reason_code,
                    occurred_at,
                    occurred_at,
                ),
            )
            created = bool(cursor.rowcount)
            row = conn.execute(
                """
                select incident_id from auth_recovery_incidents
                where platform = ? and outage_epoch = ?
                """,
                (platform, outage_epoch),
            ).fetchone()
            if row is None:
                raise RuntimeError("auth recovery incident was not persisted")
            incident_id = str(row["incident_id"])
            conn.execute(
                """
                insert or ignore into auth_recovery_incident_tasks (
                  incident_id, task_id, created_at
                ) values (?, ?, ?)
                """,
                (incident_id, task_id, occurred_at),
            )
            conn.execute("commit")
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()
        incident = self.get(incident_id)
        if incident is None:
            raise RuntimeError("auth recovery incident disappeared")
        return incident, created

    @staticmethod
    def open_manual_and_link_to_connection(
        conn: sqlite3.Connection,
        *,
        platform: str,
        reason_code: str,
        task_id: str,
        chain_id: str | None,
        occurred_at: str,
    ) -> dict[str, Any]:
        """Open the manual circuit and link its incident in the caller transaction."""
        row = conn.execute(
            """
            select state, outage_epoch from runtime_circuits
            where scope_kind = 'platform' and scope_key = ?
            """,
            (platform,),
        ).fetchone()
        outage_epoch = int(row["outage_epoch"] if row else 0)
        if row is None or row["state"] == "closed":
            outage_epoch += 1
        conn.execute(
            """
            insert into runtime_circuits (
              scope_kind, scope_key, state, recovery_mode, outage_epoch,
              consecutive_failures, opened_at, cooldown_until,
              last_failure_at, reason_code, user_action, updated_at
            ) values (
              'platform', ?, 'open', 'manual', ?, 1, ?, null, ?, ?,
              'login_or_config_required', ?
            )
            on conflict(scope_kind, scope_key) do update set
              state = 'open', recovery_mode = 'manual',
              outage_epoch = excluded.outage_epoch,
              consecutive_failures = runtime_circuits.consecutive_failures + 1,
              opened_at = case
                when runtime_circuits.state = 'closed' then excluded.opened_at
                else coalesce(runtime_circuits.opened_at, excluded.opened_at)
              end,
              cooldown_until = null, last_failure_at = excluded.last_failure_at,
              reason_code = excluded.reason_code, user_action = excluded.user_action,
              probe_owner_token = null, probe_acquired_at = null,
              probe_lease_expires_at = null, updated_at = excluded.updated_at
            """,
            (
                platform,
                outage_epoch,
                occurred_at,
                occurred_at,
                reason_code,
                occurred_at,
            ),
        )
        if row is None or row["state"] != "open":
            TaskEventRepository.append_to_connection(
                conn,
                event_type="circuit_opened",
                task_id=task_id,
                chain_id=chain_id,
                failure_class=reason_code,
                payload={
                    "version": 1,
                    "scope_kind": "platform",
                    "scope_key": platform,
                    "reason_code": reason_code,
                    "outage_epoch": outage_epoch,
                },
                occurred_at=occurred_at,
            )
        result = {
            "platform": platform,
            "outage_epoch": outage_epoch,
            "incident_id": None,
        }
        if reason_code != "platform_not_logged_in":
            return result
        proposed_id = f"authrec_{uuid4().hex}"
        conn.execute(
            """
            insert or ignore into auth_recovery_incidents (
              incident_id, platform, outage_epoch, reason_code, status,
              created_at, updated_at, notification_status
            ) values (?, ?, ?, ?, 'awaiting_user', ?, ?, 'pending')
            """,
            (
                proposed_id,
                platform,
                outage_epoch,
                reason_code,
                occurred_at,
                occurred_at,
            ),
        )
        incident = conn.execute(
            """
            select incident_id from auth_recovery_incidents
            where platform = ? and outage_epoch = ?
            """,
            (platform, outage_epoch),
        ).fetchone()
        if incident is None:
            raise RuntimeError("auth recovery incident was not persisted")
        incident_id = str(incident["incident_id"])
        conn.execute(
            """
            insert or ignore into auth_recovery_incident_tasks (
              incident_id, task_id, created_at
            ) values (?, ?, ?)
            """,
            (incident_id, task_id, occurred_at),
        )
        result["incident_id"] = incident_id
        return result

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                """
                select incident.*,
                  (select count(*) from auth_recovery_incident_tasks linked
                   where linked.incident_id = incident.incident_id)
                    as affected_task_count
                from auth_recovery_incidents incident
                where incident.incident_id = ?
                """,
                (incident_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_actionable(self, *, limit: int = 100) -> list[ActionableAuthRecoveryRow]:
        """Return unresolved incidents for passive authenticated projections."""
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select incident.*,
                  (select count(*) from auth_recovery_incident_tasks linked
                   where linked.incident_id = incident.incident_id)
                    as affected_task_count
                from auth_recovery_incidents incident
                where incident.status in ('awaiting_user', 'verifying')
                order by incident.updated_at desc, incident.incident_id
                limit ?
                """,
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [cast(ActionableAuthRecoveryRow, dict(row)) for row in rows]

    def claim_notification(
        self,
        incident_id: str,
        *,
        lease_until: str,
        updated_at: str,
    ) -> str | None:
        claim_token = uuid4().hex
        conn = self._write_connection()
        try:
            conn.execute("begin immediate")
            cursor = conn.execute(
                """
                update auth_recovery_incidents
                set notification_status = 'pending',
                    notification_attempts = notification_attempts + 1,
                    notification_lease_until = ?,
                    notification_claim_token = ?, updated_at = ?
                where incident_id = ? and notification_attempts < 5
                  and status in ('awaiting_user', 'verifying')
                  and notification_status in ('pending', 'failed')
                  and (
                    notification_lease_until is null
                    or notification_lease_until <= ?
                  )
                """,
                (lease_until, claim_token, updated_at, incident_id, updated_at),
            )
            conn.execute("commit")
            return claim_token if cursor.rowcount else None
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def complete_notification(
        self,
        incident_id: str,
        *,
        claim_token: str,
        status: str,
        notified_at: str | None,
        updated_at: str,
    ) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update auth_recovery_incidents
                set notification_status = case when notification_status='sent' then 'sent' else ? end,
                    notified_at = coalesce(notified_at, ?),
                    notification_lease_until = null,
                    notification_claim_token = null, updated_at = ?
                where incident_id = ? and notification_claim_token = ?
                  and status in ('awaiting_user', 'verifying')
                """,
                (status, notified_at, updated_at, incident_id, claim_token),
            )
        return bool(cursor.rowcount)

    def list_notification_candidates(
        self,
        *,
        now: str,
        limit: int = 10,
    ) -> list[str]:
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select incident_id from auth_recovery_incidents
                where notification_status in ('pending', 'failed')
                  and status in ('awaiting_user', 'verifying')
                  and notification_attempts < 5
                  and (
                    notification_lease_until is null
                    or notification_lease_until <= ?
                  )
                order by updated_at, incident_id
                limit ?
                """,
                (now, max(1, min(int(limit), 100))),
            ).fetchall()
        return [str(row["incident_id"]) for row in rows]

    def requeue_skipped_notifications(self, *, updated_at: str) -> int:
        """Retry actionable incidents after delivery configuration becomes ready."""
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update auth_recovery_incidents
                set notification_status = 'pending',
                    notification_lease_until = null,
                    notification_claim_token = null,
                    updated_at = ?
                where notification_status = 'skipped'
                  and status in ('awaiting_user', 'verifying')
                  and notification_attempts < 5
                """,
                (updated_at,),
            )
        return int(cursor.rowcount)

    def begin_verification(
        self,
        incident_id: str,
        *,
        updated_at: str,
        stale_before: str,
    ) -> dict[str, Any] | None:
        claim_token = uuid4().hex
        conn = self._write_connection()
        try:
            conn.execute("begin immediate")
            cursor = conn.execute(
                """
                update auth_recovery_incidents set
                  status = 'verifying', verification_started_at = ?,
                  verification_claim_token = ?,
                  last_error_code = null, updated_at = ?
                where incident_id = ? and (
                  status = 'awaiting_user'
                  or (
                    status = 'verifying'
                    and (
                      verification_started_at is null
                      or verification_started_at <= ?
                    )
                  )
                )
                """,
                (
                    updated_at,
                    claim_token,
                    updated_at,
                    incident_id,
                    stale_before,
                ),
            )
            if not cursor.rowcount:
                conn.execute("rollback")
                return None
            row = conn.execute(
                """
                select incident.*,
                  (select count(*) from auth_recovery_incident_tasks linked
                   where linked.incident_id = incident.incident_id)
                    as affected_task_count
                from auth_recovery_incidents incident
                where incident.incident_id = ?
                """,
                (incident_id,),
            ).fetchone()
            conn.execute("commit")
            return dict(row) if row else None
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def verification_failed(
        self,
        incident_id: str,
        *,
        claim_token: str,
        error_code: str,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update auth_recovery_incidents
                set status = 'awaiting_user', verification_started_at = null,
                    verification_claim_token = null,
                    last_error_code = ?, updated_at = ?
                where incident_id = ? and status = 'verifying'
                  and verification_claim_token = ?
                """,
                (error_code, updated_at, incident_id, claim_token),
            )
        return self.get(incident_id) if cursor.rowcount else None

    def open_workflow_connection(self) -> sqlite3.Connection:
        """Open a write connection whose transaction is owned by the application."""
        return self._write_connection()

    @staticmethod
    def verification_claim_matches_to_connection(
        conn: sqlite3.Connection,
        *,
        incident_id: str,
        claim_token: str,
        platform: str,
        outage_epoch: int,
    ) -> bool:
        row = conn.execute(
            """
            select 1 from auth_recovery_incidents
            where incident_id = ? and status = 'verifying'
              and verification_claim_token = ?
              and platform = ? and outage_epoch = ?
            """,
            (incident_id, claim_token, platform, outage_epoch),
        ).fetchone()
        return row is not None

    @staticmethod
    def linked_task_readiness_to_connection(
        conn: sqlite3.Connection,
        incident_id: str,
    ) -> tuple[int, int]:
        row = conn.execute(
            """
            select count(*) as total,
              sum(case
                when task.status = 'blocked'
                  and task.task_type = 'fetch'
                  and task.last_failure_class = 'platform_not_logged_in'
                then 1 else 0 end) as ready
            from auth_recovery_incident_tasks linked
            join tasks task on task.task_id = linked.task_id
            where linked.incident_id = ?
            """,
            (incident_id,),
        ).fetchone()
        return row["total"], int(row["ready"] or 0)

    @staticmethod
    def reset_tasks_not_ready_to_connection(
        conn: sqlite3.Connection,
        *,
        incident_id: str,
        claim_token: str,
        updated_at: str,
    ) -> bool:
        cursor = conn.execute(
            """
            update auth_recovery_incidents
            set status = 'awaiting_user',
                verification_started_at = null,
                verification_claim_token = null,
                last_error_code = 'recovery_tasks_not_ready',
                updated_at = ?
            where incident_id = ? and status = 'verifying'
              and verification_claim_token = ?
            """,
            (updated_at, incident_id, claim_token),
        )
        return bool(cursor.rowcount)

    @staticmethod
    def close_manual_circuit_to_connection(
        conn: sqlite3.Connection,
        *,
        platform: str,
        outage_epoch: int,
        updated_at: str,
    ) -> bool:
        cursor = conn.execute(
            """
            update runtime_circuits
            set state = 'closed', recovery_mode = 'automatic',
                consecutive_failures = 0, opened_at = null,
                cooldown_until = null, last_success_at = ?,
                reason_code = null, user_action = null,
                probe_owner_token = null, probe_acquired_at = null,
                probe_lease_expires_at = null, updated_at = ?
            where scope_kind = 'platform' and scope_key = ?
              and state = 'open' and recovery_mode = 'manual'
              and outage_epoch = ?
            """,
            (updated_at, updated_at, platform, outage_epoch),
        )
        return bool(cursor.rowcount)

    @staticmethod
    def list_recoverable_tasks_to_connection(
        conn: sqlite3.Connection,
        incident_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            select task.task_id,
                   json_extract(task.artifacts_summary, '$.chain_id') as chain_id
            from tasks task
            join auth_recovery_incident_tasks linked
              on linked.task_id = task.task_id
            where linked.incident_id = ?
              and task.status = 'blocked'
              and task.task_type = 'fetch'
              and task.last_failure_class = 'platform_not_logged_in'
            """,
            (incident_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def requeue_task_to_connection(
        conn: sqlite3.Connection,
        *,
        task_id: str,
        updated_at: str,
        next_run_at: str | None = None,
    ) -> bool:
        cursor = conn.execute(
            """
            update tasks
            set status = 'pending', attempt = attempt + 1,
                max_attempts = max(max_attempts, attempt + 1),
                next_run_at = ?, error_reason = null,
                last_failure_class = null, claimed_by = null,
                lease_expires_at = null, heartbeat_at = null,
                started_at = null, finished_at = null,
                result_version = null, completion_token = null,
                updated_at = ?
            where task_id = ? and status = 'blocked'
              and task_type = 'fetch'
              and last_failure_class = 'platform_not_logged_in'
            """,
            (next_run_at or updated_at, updated_at, task_id),
        )
        return bool(cursor.rowcount)

    @staticmethod
    def resolve_incident_to_connection(
        conn: sqlite3.Connection,
        *,
        incident_id: str,
        claim_token: str,
        platform: str,
        outage_epoch: int,
        recovered_task_count: int,
        updated_at: str,
    ) -> bool:
        cursor = conn.execute(
            """
            update auth_recovery_incidents
            set status = 'resolved', resolved_at = ?, updated_at = ?,
                verification_started_at = null, last_error_code = null,
                verification_claim_token = null,
                recovered_task_count = ?,
                notification_status = case
                  when notification_status in ('pending', 'failed')
                  then 'skipped' else notification_status end,
                notification_lease_until = null,
                notification_claim_token = null
            where incident_id = ? and status = 'verifying'
              and platform = ? and outage_epoch = ?
              and verification_claim_token = ?
            """,
            (
                updated_at,
                updated_at,
                recovered_task_count,
                incident_id,
                platform,
                outage_epoch,
                claim_token,
            ),
        )
        return bool(cursor.rowcount)

    def _write_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        conn.execute("pragma busy_timeout = 30000")
        return conn
