"""TaskMaintenanceMixin for durable task storage."""
from __future__ import annotations

import json
from typing import Any, Iterable

from backend.domain.tasks import TaskStatus
from backend.domain.timestamps import utc_now_seconds_z


class TaskMaintenanceMixin:

    def retry(self, task_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update tasks set
                    status = ?,
                    attempt = attempt + 1,
                    error_reason = null,
                    last_failure_class = null,
                    updated_at = ?
                where task_id = ?
                  and status in (?, ?, ?)
                """,
                (
                    TaskStatus.PENDING.value,
                    utc_now_seconds_z(),
                    task_id,
                    TaskStatus.FAILED.value,
                    TaskStatus.BLOCKED.value,
                    TaskStatus.SKIPPED.value,
                ),
            )
        return cursor.rowcount > 0


    def reschedule_existing(
        self,
        task_id: str,
        *,
        next_run_at: str,
        reset_attempts: bool = False,
        required_statuses: Iterable[TaskStatus | str] | None = None,
        artifacts_summary: dict[str, Any] | None = None,
    ) -> bool:
        """Explicitly reschedule a durable task outside idempotent enqueue paths."""
        status_values = [
            status.value if isinstance(status, TaskStatus) else str(status)
            for status in (required_statuses or ())
        ]
        status_clause, status_params = "", []
        if required_statuses is not None:
            if not status_values:
                return False
            status_clause = " and status in (" + ", ".join("?" for _ in status_values) + ")"
            status_params = status_values
        assignments = ["next_run_at = ?", "updated_at = ?"]
        params: list[Any] = [next_run_at, utc_now_seconds_z()]
        if reset_attempts:
            assignments.extend([
                "status = ?", "attempt = 1", "error_reason = null", "claimed_by = null",
                "lease_expires_at = null", "heartbeat_at = null", "completion_token = null",
            ])
            params.append(TaskStatus.PENDING.value)
        if artifacts_summary is not None:
            assignments.append("artifacts_summary = ?")
            params.append(json.dumps(artifacts_summary))
        with self._connect() as conn:
            cursor = conn.execute(
                f"update tasks set {', '.join(assignments)} where task_id = ?" + status_clause,
                (*params, task_id, *status_params),
            )
        return cursor.rowcount > 0
