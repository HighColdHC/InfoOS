"""One-time-compatible reconciliation for manual login recovery."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


class AuthRecoveryReconciliationRepository(InfoOSRepository):
    def __init__(self, base_dir: Path | str):
        super().__init__(base_dir)

    def reconcile_manual_login_circuits(
        self,
        *,
        updated_at: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Backfill incidents and park active fetches for pre-incident circuits."""
        conn = self._write_connection()
        changed_incident_ids: list[str] = []
        try:
            conn.execute("begin immediate")
            circuits = conn.execute(
                """
                select scope_key as platform, outage_epoch
                from runtime_circuits
                where scope_kind = 'platform'
                  and state = 'open'
                  and recovery_mode = 'manual'
                  and reason_code = 'platform_not_logged_in'
                order by updated_at, scope_key
                limit ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
            for circuit in circuits:
                incident_id = self._reconcile_circuit(
                    conn,
                    platform=str(circuit["platform"]),
                    outage_epoch=int(circuit["outage_epoch"]),
                    updated_at=updated_at,
                )
                if incident_id is not None:
                    changed_incident_ids.append(incident_id)
            conn.execute("commit")
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()
        incident_repo = AuthRecoveryRepository(self.base_dir)
        return [
            incident
            for incident_id in changed_incident_ids
            if (incident := incident_repo.get(incident_id)) is not None
        ]

    def _reconcile_circuit(
        self,
        conn: sqlite3.Connection,
        *,
        platform: str,
        outage_epoch: int,
        updated_at: str,
    ) -> str | None:
        task_rows = conn.execute(
            """
            select task.task_id,
                   json_extract(task.artifacts_summary, '$.chain_id') as chain_id
            from tasks task
            join sources source
              on task.target_kind = 'source'
             and task.target_id = source.source_id
            where task.task_type = 'fetch'
              and task.status = 'pending'
              and source.enabled = 1
              and source.platform = ?
            order by task.created_at, task.task_id
            """,
            (platform,),
        ).fetchall()
        if not task_rows:
            return None
        incident_id = self._ensure_incident(
            conn,
            platform=platform,
            outage_epoch=outage_epoch,
            updated_at=updated_at,
        )
        changed = False
        for task_row in task_rows:
            task_id = str(task_row["task_id"])
            cursor = conn.execute(
                """
                update tasks
                set status = 'blocked', next_run_at = null,
                    error_reason = 'Platform login required',
                    last_failure_class = 'platform_not_logged_in',
                    claimed_by = null, lease_expires_at = null,
                    heartbeat_at = null, completion_token = null,
                    updated_at = ?
                where task_id = ? and status = 'pending'
                """,
                (updated_at, task_id),
            )
            if not cursor.rowcount:
                continue
            changed = True
            conn.execute(
                """
                insert or ignore into auth_recovery_incident_tasks (
                  incident_id, task_id, created_at
                ) values (?, ?, ?)
                """,
                (incident_id, task_id, updated_at),
            )
            TaskEventRepository.append_to_connection(
                conn,
                event_type="auth_recovery_required",
                task_id=task_id,
                chain_id=task_row["chain_id"],
                status="blocked",
                failure_class="platform_not_logged_in",
                payload={
                    "version": 1,
                    "incident_id": incident_id,
                    "platform": platform,
                    "outage_epoch": outage_epoch,
                },
                occurred_at=updated_at,
            )
        return incident_id if changed else None

    @staticmethod
    def _ensure_incident(
        conn: sqlite3.Connection,
        *,
        platform: str,
        outage_epoch: int,
        updated_at: str,
    ) -> str:
        proposed_id = f"authrec_{uuid4().hex}"
        conn.execute(
            """
            insert or ignore into auth_recovery_incidents (
              incident_id, platform, outage_epoch, reason_code, status,
              created_at, updated_at, notification_status
            ) values (
              ?, ?, ?, 'platform_not_logged_in', 'awaiting_user',
              ?, ?, 'pending'
            )
            """,
            (
                proposed_id,
                platform,
                outage_epoch,
                updated_at,
                updated_at,
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
        return str(incident["incident_id"])

    def _write_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma foreign_keys = on")
        conn.execute("pragma busy_timeout = 30000")
        return conn
