"""Atomic terminal task transitions and their downstream queue writes."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from backend.domain.tasks import (
    DependencySpec,
    Task,
    TaskStatus,
    TaskType,
    new_task_completion_token,
)
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.task_queue_policy import queue_policy_for


def enqueue_task(conn: sqlite3.Connection, task: Task, *, now: str) -> None:
    policy = queue_policy_for(conn, task, enqueued_at=now)
    deps_json = json.dumps([
        {"task_id": dependency.task_id, "required_status": dependency.required_status.value}
        for dependency in task.depends_on
    ])
    conn.execute(
        """
        insert into tasks (
            task_id, task_type, target_kind, target_id, status,
            depends_on, attempt, max_attempts, next_run_at,
            error_reason, artifacts_summary, claimed_by, lease_expires_at,
            heartbeat_at, started_at, finished_at, input_version,
            result_version, last_failure_class, completion_token,
            queue_name, priority, deadline_at, created_at, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(task_id) do nothing
        """,
        (
            task.task_id, task.task_type.value, task.target_kind, task.target_id,
            task.status.value, deps_json, task.attempt, task.max_attempts,
            task.next_run_at, task.error_reason, json.dumps(task.artifacts_summary),
            task.claimed_by, task.lease_expires_at, task.heartbeat_at, task.started_at,
            task.finished_at, task.input_version, task.result_version,
            task.last_failure_class, task.completion_token,
            policy.queue_name, policy.priority, policy.deadline_at, now, now,
        ),
    )


def _row_to_task(row: sqlite3.Row) -> Task:
    depends_on = tuple(
        DependencySpec(task_id=item["task_id"], required_status=TaskStatus(item["required_status"]))
        for item in json.loads(row["depends_on"])
    )
    return Task(
        task_id=row["task_id"], task_type=TaskType(row["task_type"]),
        target_kind=row["target_kind"], target_id=row["target_id"], status=TaskStatus(row["status"]),
        depends_on=depends_on, attempt=row["attempt"], max_attempts=row["max_attempts"],
        next_run_at=row["next_run_at"], error_reason=row["error_reason"],
        artifacts_summary=json.loads(row["artifacts_summary"]), claimed_by=row["claimed_by"],
        lease_expires_at=row["lease_expires_at"], heartbeat_at=row["heartbeat_at"],
        started_at=row["started_at"], finished_at=row["finished_at"], input_version=row["input_version"],
        result_version=row["result_version"], last_failure_class=row["last_failure_class"],
        completion_token=row["completion_token"],
        queue_name=row["queue_name"], priority=row["priority"], deadline_at=row["deadline_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


class TaskFinalizationQueue:
    """The task-table operations permitted while finalizing one task."""

    def __init__(self, conn: sqlite3.Connection, *, now: str):
        self._conn = conn
        self._now = now

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the active terminal-transition transaction to batch writes."""
        return self._conn

    def enqueue(self, task: Task) -> None:
        enqueue_task(self._conn, task, now=self._now)

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute("select * from tasks where task_id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_by_target(
        self,
        *,
        target_kind: str,
        target_id: str,
        task_type: TaskType | None = None,
    ) -> list[Task]:
        clauses = ["target_kind = ?", "target_id = ?"]
        params: list[str] = [target_kind, target_id]
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type.value)
        rows = self._conn.execute(
            "select * from tasks where " + " and ".join(clauses)
            + " order by next_run_at, created_at, task_id",
            tuple(params),
        ).fetchall()
        return [_row_to_task(row) for row in rows]


class TaskFinalizationMixin:
    def finalize_terminal_with_downstream(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: TaskStatus,
        artifacts: dict[str, Any],
        error_reason: str | None,
        result_version: str | None,
        finished_at: str,
        enqueue_downstream: Callable[[TaskFinalizationQueue], list[str]],
        attempt: int | None = None,
        failure_class: str | None = None,
        clear_next_run_at: bool = False,
        preserve_result_metadata: bool = False,
        event_chain_id: str | None = None,
        event_payload: dict[str, Any] | None = None,
        event_failure_class: str | None = None,
        required_resource_owner: tuple[str, str, str] | None = None,
        transaction_callback: Callable[[sqlite3.Connection], None] | None = None,
        lease_expired_before: str | None = None,
    ) -> tuple[dict[str, Any], list[str]] | None:
        """Atomically commit one owned terminal attempt and all of its child tasks.

        Resource-bound tasks must still own their exclusive runtime resource in
        this same transaction. This closes the final race between the handler's
        last ownership check and the terminal write.
        """
        conn = self._connect_for_claim()
        try:
            conn.execute("begin immediate")
            sets = [
                "status = ?", "artifacts_summary = ?", "error_reason = ?",
                "claimed_by = null", "lease_expires_at = null",
                "heartbeat_at = null", "completion_token = ?", "updated_at = ?",
            ]
            completion_token = new_task_completion_token()
            params: list[Any] = [
                status.value, json.dumps(artifacts), error_reason, completion_token, finished_at,
            ]
            if not preserve_result_metadata:
                sets.append("result_version = ?")
                params.append(result_version)
                sets.append("finished_at = ?")
                params.append(finished_at)
            if attempt is not None:
                sets.append("attempt = ?")
                params.append(attempt)
            if status == TaskStatus.SUCCESS:
                sets.append("last_failure_class = null")
            elif failure_class is not None:
                sets.append("last_failure_class = ?")
                params.append(failure_class)
            if clear_next_run_at:
                sets.append("next_run_at = null")
            resource_clause = ""
            resource_params: tuple[str, ...] = ()
            if required_resource_owner is not None:
                resource_kind, resource_key, owner_token = required_resource_owner
                resource_clause = """
                  and exists (
                    select 1 from runtime_resource_leases r
                    where r.resource_kind = ? and r.resource_key = ?
                      and r.owner_token = ? and r.task_id = tasks.task_id
                  )
                """
                resource_params = (resource_kind, resource_key, owner_token)
            expired_clause = " and lease_expires_at < ?" if lease_expired_before is not None else ""
            cursor = conn.execute(
                "update tasks set " + ", ".join(sets)
                + " where task_id = ? and status = ? and claimed_by = ?"
                + resource_clause + expired_clause,
                (
                    *params,
                    task_id,
                    TaskStatus.RUNNING.value,
                    worker_id,
                    *resource_params,
                    *((lease_expired_before,) if lease_expired_before is not None else ()),
                ),
            )
            if not cursor.rowcount:
                conn.execute("rollback")
                return None
            from backend.infrastructure.storage.capture_receipts import CaptureReceiptRepository
            capture_identity = CaptureReceiptRepository.finalize_root_to_connection(
                conn, task_id, status=status.value,
                error_code=failure_class or (error_reason if error_reason and error_reason.isidentifier() else None),
                finished_at=finished_at,
            )
            artifacts.update(capture_identity)
            downstream_ids = enqueue_downstream(TaskFinalizationQueue(conn, now=finished_at))
            durable_artifacts = {**artifacts, "downstream_task_ids": downstream_ids}
            durable = conn.execute(
                """
                update tasks set artifacts_summary = ?, updated_at = ?
                where task_id = ? and status = ? and completion_token = ?
                """,
                (json.dumps(durable_artifacts), finished_at, task_id, status.value, completion_token),
            )
            if not durable.rowcount:
                conn.execute("rollback")
                return None
            if event_payload is not None:
                TaskEventRepository.append_to_connection(
                    conn,
                    event_type={
                        TaskStatus.SUCCESS: "completed",
                        TaskStatus.BLOCKED: "blocked",
                        TaskStatus.FAILED: "failed",
                        TaskStatus.SKIPPED: "skipped",
                    }.get(status, "completed"),
                    task_id=task_id,
                    chain_id=event_chain_id,
                    status=status.value,
                    failure_class=event_failure_class,
                    payload={**event_payload, "downstream_count": len(downstream_ids)},
                    occurred_at=finished_at,
                )
            if transaction_callback is not None:
                transaction_callback(conn)
            conn.execute("commit")
            return durable_artifacts, downstream_ids
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()
