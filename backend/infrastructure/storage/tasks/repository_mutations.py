"""TaskMutationMixin for durable task storage."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.domain.tasks import Task, TaskStatus, new_task_completion_token
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.storage.tasks.finalization import enqueue_task
from backend.infrastructure.storage.platform_fetch_recovery import source_has_circuit_waiting_fetch


class TaskMutationMixin:

    def enqueue(self, task: Task) -> None:
        self.enqueue_many([task])


    def enqueue_many(
        self,
        tasks: list[Task],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is not None:
            for task in tasks:
                enqueue_task(conn, task, now=utc_now_seconds_z())
            return
        with self._connect() as own_conn:
            for task in tasks:
                enqueue_task(own_conn, task, now=utc_now_seconds_z())


    def enqueue_periodic_fetch_singleflight(
        self,
        tasks: list[Task],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> list[str]:
        """Atomically insert automatic FETCH roots only while no peer is active."""
        if not tasks:
            return []

        def insert_available(active_conn: sqlite3.Connection) -> list[str]:
            inserted: list[str] = []
            for task in tasks:
                if not task.artifacts_summary.get("manual_run") and source_has_circuit_waiting_fetch(active_conn, task.target_id):
                    continue
                active = active_conn.execute(
                    """
                    select 1 from tasks
                    where target_kind = 'source'
                      and target_id = ?
                      and task_type = 'fetch'
                      and input_version = ?
                      and status in ('pending', 'running')
                      and coalesce(json_extract(artifacts_summary, '$.manual_run'), 0) != 1
                    limit 1
                    """,
                    (task.target_id, task.input_version),
                ).fetchone()
                if active is not None:
                    continue
                already_exists = active_conn.execute(
                    "select 1 from tasks where task_id = ?", (task.task_id,)
                ).fetchone()
                if already_exists is not None:
                    continue
                enqueue_task(active_conn, task, now=utc_now_seconds_z())
                inserted.append(task.task_id)
            return inserted

        if conn is not None:
            return insert_available(conn)
        own_conn = self._connect_for_claim()
        try:
            own_conn.execute("begin immediate")
            inserted = insert_available(own_conn)
            own_conn.execute("commit")
            return inserted
        except Exception:
            if own_conn.in_transaction:
                own_conn.execute("rollback")
            raise
        finally:
            own_conn.close()


    def update_artifacts(self, task_id: str, artifacts: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update tasks set artifacts_summary = ?, updated_at = ?
                where task_id = ?
                """,
                (json.dumps(artifacts), utc_now_seconds_z(), task_id),
            )


    def defer(
        self,
        task_id: str,
        *,
        next_run_at: str,
        error_reason: str | None = None,
        artifacts_summary: dict[str, Any] | None = None,
        worker_id: str | None = None,
    ) -> bool:
        sets = [
            "status = ?", "next_run_at = ?", "claimed_by = null",
            "lease_expires_at = null", "heartbeat_at = null", "completion_token = null", "updated_at = ?",
        ]
        params: list[Any] = [TaskStatus.PENDING.value, next_run_at, utc_now_seconds_z()]
        if error_reason is not None:
            sets.append("error_reason = ?")
            params.append(error_reason)
        if artifacts_summary is not None:
            sets.append("artifacts_summary = ?")
            params.append(json.dumps(artifacts_summary))
        params.append(task_id)
        where = "task_id = ?"
        if worker_id is not None:
            where += " and status = ? and claimed_by = ?"
            params.extend([TaskStatus.RUNNING.value, worker_id])
        with self._connect() as conn:
            cursor = conn.execute(
                f"update tasks set {', '.join(sets)} where {where}",
                tuple(params),
            )
        return cursor.rowcount > 0


    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error_reason: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                update tasks set
                    status = ?,
                    error_reason = ?,
                    completion_token = null,
                    updated_at = ?
                where task_id = ?
                """,
                (status.value, error_reason, utc_now_seconds_z(), task_id),
            )


    def record_failure(self, task: Task, *, error_reason: str) -> TaskStatus:
        next_attempt = int(task.attempt) + 1
        status = TaskStatus.FAILED if task.attempt >= task.max_attempts else TaskStatus.PENDING
        with self._connect() as conn:
            conn.execute(
                """
                update tasks set
                    status = ?,
                    attempt = ?,
                    error_reason = ?,
                    completion_token = null,
                    updated_at = ?
                where task_id = ?
                """,
                (status.value, next_attempt, error_reason, utc_now_seconds_z(), task.task_id),
            )
        return status


    def fail_or_retry(
        self,
        task_id: str,
        *,
        worker_id: str,
        decision: "TaskRetryDecision",
        error_reason: str,
        updated_at: str,
        artifacts: dict[str, Any] | None = None,
        completion_token: str | None = None,
        event_chain_id: str | None = None,
        event_payload: dict[str, Any] | None = None,
        event_occurred_at: str | None = None,
    ) -> bool:
        """Persist a policy decision only while this worker still owns the lease."""
        token = (
            completion_token or new_task_completion_token()
            if decision.status != TaskStatus.PENDING
            else None
        )
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            cursor = conn.execute(
                """
                update tasks set
                    status = ?, attempt = ?, next_run_at = ?, error_reason = ?,
                    last_failure_class = ?, claimed_by = null, lease_expires_at = null,
                    heartbeat_at = null, completion_token = ?,
                    artifacts_summary = coalesce(?, artifacts_summary), updated_at = ?
                where task_id = ? and status = ? and claimed_by = ?
                """,
                (
                    decision.status.value,
                    decision.attempt,
                    decision.next_run_at,
                    error_reason,
                    decision.failure_class,
                    token,
                    json.dumps(artifacts) if artifacts is not None else None,
                    updated_at,
                    task_id,
                    TaskStatus.RUNNING.value,
                    worker_id,
                ),
            )
            if cursor.rowcount and event_occurred_at is not None:
                TaskEventRepository.append_to_connection(
                    conn,
                    event_type="retry_scheduled" if decision.status == TaskStatus.PENDING else decision.status.value,
                    task_id=task_id,
                    chain_id=event_chain_id,
                    status=decision.status.value,
                    failure_class=decision.failure_class,
                    payload=event_payload,
                    occurred_at=event_occurred_at,
                )
            conn.execute("commit")
            return cursor.rowcount > 0
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()
