"""Atomic task claiming and worker ownership operations."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from backend.domain.tasks import (
    DependencySpec,
    Task,
    TaskStatus,
    TaskType,
    new_task_completion_token,
)
from backend.infrastructure.storage.task_events import TaskEventRepository


def _row_to_task(row: sqlite3.Row) -> Task:
    deps_raw = json.loads(row["depends_on"])
    depends_on = tuple(
        DependencySpec(task_id=d["task_id"], required_status=TaskStatus(d["required_status"]))
        for d in deps_raw
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


def _task_type_values(task_types: Iterable[TaskType | str] | None) -> list[str] | None:
    if task_types is None:
        return None
    return [task_type.value if isinstance(task_type, TaskType) else str(task_type) for task_type in task_types]


def _eligible_claim_candidates(conn: sqlite3.Connection, candidates: Iterable[Task], *, now: str) -> list[Task]:
    """Re-read dependencies and honor circuit recovery slots inside the claim transaction."""
    candidate_list = list(candidates)
    dependency_ids = {dep.task_id for task in candidate_list for dep in task.depends_on}
    completed: dict[str, TaskStatus] = {}
    if dependency_ids:
        placeholders = ", ".join("?" for _ in dependency_ids)
        rows = conn.execute(
            "select task_id, status from tasks where task_id in (" + placeholders
            + ") and status in (?, ?, ?, ?)",
            (
                *dependency_ids,
                TaskStatus.SUCCESS.value,
                TaskStatus.FAILED.value,
                TaskStatus.BLOCKED.value,
                TaskStatus.SKIPPED.value,
            ),
        ).fetchall()
        completed = {row["task_id"]: TaskStatus(row["status"]) for row in rows}
    return [task for task in candidate_list if task.can_run(completed)
            and str(task.artifacts_summary.get("platform_recovery_not_before") or "") <= now]


class TaskClaimMixin:
    """Lease-backed claim and completion methods for TaskRepository."""

    def claim_task_ids(self, task_ids: list[str], *, worker_id: str, now: str, lease_expires_at: str,
        task_types: Iterable[TaskType | str] | None = None,
        queue_names: Iterable[str] | None = None,
        event_occurred_at: str | None = None,
    ) -> list[Task]:
        task_type_values = _task_type_values(task_types)
        queue_values = list(queue_names) if queue_names is not None else None
        if not task_ids or task_type_values == [] or queue_values == []:
            return []
        type_clause = ""
        type_params: list[str] = []
        if task_type_values is not None:
            type_clause = " and task_type in (" + ", ".join("?" for _ in task_type_values) + ")"
            type_params = task_type_values
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
            candidates_by_id = {row["task_id"]: _row_to_task(row) for row in rows}
            candidates = (
                candidates_by_id[task_id]
                for task_id in task_ids
                if task_id in candidates_by_id
            )
            claimed: list[Task] = []
            for task in _eligible_claim_candidates(conn, candidates, now=event_occurred_at or now):
                cursor = conn.execute(
                    """
                    update tasks set status = 'running', claimed_by = ?, lease_expires_at = ?,
                        heartbeat_at = ?, started_at = ?, completion_token = null, updated_at = ?
                    where task_id = ? and status = 'pending'
                    """ + type_clause + queue_clause,
                    (
                        worker_id, lease_expires_at, now, now, now, task.task_id,
                        *type_params, *queue_params,
                    ),
                )
                if cursor.rowcount:
                    claimed_task = _row_to_task(conn.execute(
                        "select * from tasks where task_id = ?", (task.task_id,)
                    ).fetchone())
                    if event_occurred_at is not None:
                        TaskEventRepository.append_to_connection(
                            conn,
                            event_type="claimed",
                            task_id=claimed_task.task_id,
                            chain_id=claimed_task.artifacts_summary.get("chain_id"),
                            status=TaskStatus.RUNNING.value,
                            payload={"attempt": claimed_task.attempt, "worker_id": claimed_task.claimed_by},
                            occurred_at=event_occurred_at,
                        )
                    claimed.append(claimed_task)
            conn.execute("commit")
            return claimed
        except Exception:
            if conn.in_transaction:
                conn.execute("rollback")
            raise
        finally:
            conn.close()

    def heartbeat(self, task_id: str, *, worker_id: str, now: str, lease_expires_at: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update tasks set
                    heartbeat_at = case
                        when heartbeat_at is null or heartbeat_at < ? then ? else heartbeat_at end,
                    lease_expires_at = case
                        when lease_expires_at is null or lease_expires_at < ? then ? else lease_expires_at end,
                    updated_at = case
                        when updated_at < ? then ? else updated_at end
                where task_id = ? and status = 'running' and claimed_by = ?
                """,
                (now, now, lease_expires_at, lease_expires_at, now, now, task_id, worker_id),
            )
        return cursor.rowcount > 0

    def complete(
        self,
        task_id: str,
        *,
        worker_id: str,
        status: TaskStatus,
        artifacts: dict[str, Any],
        error_reason: str | None,
        result_version: str | None,
        finished_at: str,
        completion_token: str | None = None,
    ) -> bool:
        token = completion_token or new_task_completion_token()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update tasks set status = ?, artifacts_summary = ?, error_reason = ?, result_version = ?,
                    finished_at = ?, claimed_by = null, lease_expires_at = null,
                    heartbeat_at = null, completion_token = ?, updated_at = ?
                where task_id = ? and status = 'running' and claimed_by = ?
                """,
                (
                    status.value, json.dumps(artifacts), error_reason, result_version,
                    finished_at, token, finished_at, task_id, worker_id,
                ),
            )
        return cursor.rowcount > 0
