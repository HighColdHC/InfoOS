"""Safe recovery decisions for non-failure task deferrals."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.domain.tasks import TaskStatus
from backend.domain.task_resources import ResourceLease
from backend.infrastructure.storage.task_events import TaskEventRepository

if TYPE_CHECKING:
    from backend.domain.tasks import Task
    from backend.domain.failures.facts import RecoveryDecision



class ResourceRecoveryMixin:
    def defer_owned_for_circuit(
        self, task: Task, *, worker_id: str, next_run_at: str,
        occurred_at: str, scope_kind: str, scope_key: str,
        resource: ResourceLease | None = None,
    ) -> bool:
        """Return an admitted but unexecuted task and its resource atomically."""
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            cursor = conn.execute(
                """update tasks set status = ?, next_run_at = ?, updated_at = ?,
                   claimed_by = null, lease_expires_at = null, heartbeat_at = null,
                   completion_token = null
                   where task_id = ? and status = ? and claimed_by = ?""",
                (TaskStatus.PENDING.value, next_run_at, occurred_at, task.task_id,
                 TaskStatus.RUNNING.value, worker_id),
            )
            if cursor.rowcount:
                if resource is not None:
                    conn.execute(
                        """delete from runtime_resource_leases
                           where resource_kind = ? and resource_key = ? and owner_token = ?""",
                        (resource.resource_kind, resource.resource_key, resource.owner_token),
                    )
                TaskEventRepository.append_to_connection(
                    conn, event_type="recovery_decided", task_id=task.task_id,
                    chain_id=task.artifacts_summary.get("chain_id"),
                    status=TaskStatus.PENDING.value,
                    payload={"version": 1, "action": "defer", "reason": "circuit_open",
                             "next_run_at": next_run_at, "scope_kind": scope_kind,
                             "scope_key": scope_key},
                    occurred_at=occurred_at,
                )
            conn.execute("commit")
            return bool(cursor.rowcount)
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def defer_pending_for_circuit(
        self,
        task: Task,
        *,
        next_run_at: str,
        occurred_at: str,
        scope_kind: str,
        scope_key: str,
    ) -> bool:
        """Move a still-pending task once, avoiding event floods on passive polls."""
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            cursor = conn.execute(
                """
                update tasks set next_run_at = ?, updated_at = ?
                where task_id = ? and status = ?
                  and (next_run_at is null or next_run_at < ?)
                """,
                (
                    next_run_at,
                    occurred_at,
                    task.task_id,
                    TaskStatus.PENDING.value,
                    next_run_at,
                ),
            )
            if cursor.rowcount:
                TaskEventRepository.append_to_connection(
                    conn,
                    event_type="recovery_decided",
                    task_id=task.task_id,
                    chain_id=task.artifacts_summary.get("chain_id"),
                    status=TaskStatus.PENDING.value,
                    payload={
                        "version": 1,
                        "action": "defer",
                        "reason": "circuit_open",
                        "next_run_at": next_run_at,
                        "scope_kind": scope_kind,
                        "scope_key": scope_key,
                    },
                    occurred_at=occurred_at,
                )
            conn.execute("commit")
            return cursor.rowcount > 0
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def defer_resource_contention(
        self,
        task: Task,
        *,
        worker_id: str,
        next_run_at: str,
        resource_kind: str,
        resource_key: str,
        occurred_at: str,
        decision: RecoveryDecision,
    ) -> bool:
        """Defer without changing attempt/failure facts and append the decision atomically."""
        pacing = task.artifacts_summary.get("fetch_pacing")
        pacing_json = None
        if pacing is not None:
            pacing_json = json.dumps(pacing, ensure_ascii=False)
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            cursor = conn.execute(
                """
                update tasks set status = ?, next_run_at = ?,
                    artifacts_summary = case when ? is null then artifacts_summary
                        else json_set(artifacts_summary, '$.fetch_pacing', json(?)) end,
                    claimed_by = null, lease_expires_at = null,
                    heartbeat_at = null, completion_token = null, updated_at = ?
                where task_id = ? and status = ? and claimed_by = ?
                """,
                (
                    TaskStatus.PENDING.value,
                    next_run_at,
                    pacing_json,
                    pacing_json,
                    occurred_at,
                    task.task_id,
                    TaskStatus.RUNNING.value,
                    worker_id,
                ),
            )
            if cursor.rowcount:
                TaskEventRepository.append_to_connection(
                    conn,
                    event_type="resource_deferred",
                    task_id=task.task_id,
                    chain_id=task.artifacts_summary.get("chain_id"),
                    status=TaskStatus.PENDING.value,
                    payload={
                        "version": 1,
                        "resource_kind": resource_kind,
                        "resource_key": resource_key,
                    },
                    occurred_at=occurred_at,
                )
                TaskEventRepository.append_to_connection(
                    conn,
                    event_type="recovery_decided",
                    task_id=task.task_id,
                    chain_id=task.artifacts_summary.get("chain_id"),
                    status=TaskStatus.PENDING.value,
                    payload=decision.to_event_payload(),
                    occurred_at=occurred_at,
                )
            conn.execute("commit")
            return cursor.rowcount > 0
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()
