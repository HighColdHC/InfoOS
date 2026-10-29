"""Immutable resource-admission values shared by executor and storage."""
from __future__ import annotations

from typing import Iterable

from backend.infrastructure.storage.tasks.claims import (
    _eligible_claim_candidates,
    _row_to_task,
    _task_type_values,
)
from backend.domain.tasks import TaskStatus, TaskType
from backend.domain.task_resources import ResourceRequest, ResourceLease, AdmittedTask, new_resource_owner_token
from backend.infrastructure.storage.runtime_resource_leases import (
    RuntimeResourceLeaseRepository,
)
from backend.infrastructure.storage.task_events import TaskEventRepository




class ResourceAdmissionMixin:
    def heartbeat_with_resource(
        self,
        task_id: str,
        *,
        worker_id: str,
        resource: ResourceLease,
        now: str,
        lease_expires_at: str,
    ) -> bool:
        """Renew task and exclusive resource ownership in one transaction."""
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            task_owned = conn.execute(
                """
                select 1 from tasks
                where task_id = ? and status = 'running' and claimed_by = ?
                """,
                (task_id, worker_id),
            ).fetchone()
            resource_owned = conn.execute(
                """
                select 1 from runtime_resource_leases
                where resource_kind = ? and resource_key = ? and owner_token = ?
                """,
                (
                    resource.resource_kind,
                    resource.resource_key,
                    resource.owner_token,
                ),
            ).fetchone()
            if task_owned is None or resource_owned is None:
                conn.execute("commit")
                return False
            conn.execute(
                """
                update tasks set heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                where task_id = ? and status = 'running' and claimed_by = ?
                """,
                (now, lease_expires_at, now, task_id, worker_id),
            )
            conn.execute(
                """
                update runtime_resource_leases
                set lease_expires_at = ?, updated_at = ?
                where resource_kind = ? and resource_key = ? and owner_token = ?
                """,
                (
                    lease_expires_at,
                    now,
                    resource.resource_kind,
                    resource.resource_key,
                    resource.owner_token,
                ),
            )
            conn.execute("commit")
            return True
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def claim_task_ids_with_resources(
        self,
        task_ids: list[str],
        *,
        worker_id: str,
        now: str,
        lease_expires_at: str,
        resource_requests: dict[str, ResourceRequest],
        task_types: Iterable[TaskType | str] | None = None,
        queue_names: Iterable[str] | None = None,
        event_occurred_at: str | None = None,
    ) -> list[AdmittedTask]:
        """Acquire each exclusive resource and claim its task in one transaction."""
        type_values = _task_type_values(task_types)
        queue_values = list(queue_names) if queue_names is not None else None
        if not task_ids or type_values == [] or queue_values == []:
            return []
        type_clause = ""
        type_params: list[str] = []
        if type_values is not None:
            type_clause = " and task_type in (" + ", ".join("?" for _ in type_values) + ")"
            type_params = type_values
        queue_clause = ""
        queue_params: list[str] = []
        if queue_values is not None:
            queue_clause = " and queue_name in (" + ", ".join("?" for _ in queue_values) + ")"
            queue_params = queue_values
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            placeholders = ", ".join("?" for _ in task_ids)
            rows = conn.execute(
                "select * from tasks where task_id in (" + placeholders
                + ") and status = 'pending'" + type_clause + queue_clause,
                (*task_ids, *type_params, *queue_params),
            ).fetchall()
            by_id = {row["task_id"]: _row_to_task(row) for row in rows}
            eligible = _eligible_claim_candidates(
                conn, (by_id[task_id] for task_id in task_ids if task_id in by_id),
                now=event_occurred_at or now,
            )
            admitted: list[AdmittedTask] = []
            for task in eligible:
                request = resource_requests.get(task.task_id)
                if request is None:
                    continue
                owner_token = new_resource_owner_token()
                acquired_key = next(
                    (
                        resource_key
                        for resource_key in request.candidate_keys
                        if RuntimeResourceLeaseRepository.acquire_on_connection(
                            conn,
                            request.resource_kind,
                            resource_key,
                            owner_token=owner_token,
                            task_id=task.task_id,
                            acquired_at=now,
                            lease_expires_at=lease_expires_at,
                        )
                    ),
                    None,
                )
                if acquired_key is None:
                    self._record_deferred(
                        conn, task, request, occurred_at=event_occurred_at
                    )
                    continue
                cursor = conn.execute(
                    """
                    update tasks set status = 'running', claimed_by = ?,
                        lease_expires_at = ?, heartbeat_at = ?, started_at = ?,
                        completion_token = null, updated_at = ?
                    where task_id = ? and status = 'pending'
                    """ + type_clause + queue_clause,
                    (
                        worker_id, lease_expires_at, now, now, now,
                        task.task_id, *type_params, *queue_params,
                    ),
                )
                if not cursor.rowcount:
                    conn.execute(
                        """
                        delete from runtime_resource_leases
                        where resource_kind = ? and resource_key = ? and owner_token = ?
                        """,
                        (request.resource_kind, acquired_key, owner_token),
                    )
                    continue
                claimed = _row_to_task(
                    conn.execute(
                        "select * from tasks where task_id = ?", (task.task_id,)
                    ).fetchone()
                )
                resource = ResourceLease(
                    request.resource_kind,
                    acquired_key,
                    owner_token,
                )
                self._record_claimed(
                    conn, claimed, resource, occurred_at=event_occurred_at
                )
                admitted.append(
                    AdmittedTask(
                        task=claimed,
                        resource=resource,
                    )
                )
            conn.execute("commit")
            return admitted
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    @staticmethod
    def _record_deferred(conn, task, request, *, occurred_at):  # type: ignore[no-untyped-def]
        if occurred_at is None:
            return
        TaskEventRepository.append_to_connection(
            conn,
            event_type="resource_deferred",
            task_id=task.task_id,
            chain_id=task.artifacts_summary.get("chain_id"),
            status=TaskStatus.PENDING.value,
            payload={
                "version": 1,
                "resource_kind": request.resource_kind,
                "resource_key": request.contention_key,
            },
            occurred_at=occurred_at,
        )

    @staticmethod
    def _record_claimed(conn, task, resource, *, occurred_at):  # type: ignore[no-untyped-def]
        if occurred_at is None:
            return
        TaskEventRepository.append_to_connection(
            conn,
            event_type="claimed",
            task_id=task.task_id,
            chain_id=task.artifacts_summary.get("chain_id"),
            status=TaskStatus.RUNNING.value,
            payload={
                "attempt": task.attempt,
                "worker_id": task.claimed_by,
                "resource_kind": resource.resource_kind,
                "resource_key": resource.resource_key,
            },
            occurred_at=occurred_at,
        )
