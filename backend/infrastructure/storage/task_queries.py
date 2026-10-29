"""Read-only SQLite queries for task projections."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.domain.tasks import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


class TaskReadQuery:
    def __init__(self, base_dir: Path | str):
        self.db_path = resolve_sqlite_path(base_dir)

    def get(self, task_id: str) -> Task | None:
        with connect_existing(self.db_path) as conn:
            row = conn.execute("select * from tasks where task_id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def get_many(self, task_ids: list[str]) -> list[Task]:
        """Read only the requested tasks, preserving input order and omitting missing IDs."""
        if not task_ids:
            return []
        unique_ids = list(dict.fromkeys(task_ids))
        by_id: dict[str, Task] = {}
        with connect_existing(self.db_path) as conn:
            for start in range(0, len(unique_ids), 400):
                chunk = unique_ids[start:start + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "select task_id, task_type, target_kind, target_id, status, depends_on, "
                    "attempt, max_attempts, next_run_at, error_reason, artifacts_summary, "
                    "claimed_by, lease_expires_at, heartbeat_at, started_at, finished_at, "
                    "input_version, result_version, last_failure_class, completion_token, "
                    "queue_name, priority, deadline_at, created_at, updated_at "
                    f"from tasks where task_id in ({placeholders})", chunk,
                ).fetchall()
                by_id.update((row["task_id"], _row_to_task(row)) for row in rows)
        return [by_id[task_id] for task_id in task_ids if task_id in by_id]

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        limit: int | None = None,
        before: tuple[str, str] | None = None,
    ) -> list[Task]:
        filters: list[str] = []
        params: list[Any] = []
        if status is not None:
            filters.append("status = ?")
            params.append(status.value)
        if before is not None:
            filters.append("(created_at < ? or (created_at = ? and task_id > ?))")
            params.extend((before[0], before[0], before[1]))
        where = f" where {' and '.join(filters)}" if filters else ""
        sql = f"select * from tasks{where} order by created_at desc, task_id"
        if limit is not None:
            sql += " limit ?"
            params.append(int(limit))
        with connect_existing(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_task(row) for row in rows]

    def search_tasks(
        self,
        *,
        status: str | None = None,
        task_type: str | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        chain_id: str | None = None,
        source_id: str | None = None,
        failure_class: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
        limit: int = 100,
    ) -> list[Task]:
        """Filter the whole table in SQL, newest first.

        ``source_id`` matches the task's own source target or the source of the
        card it works on (the card target, else ``artifacts_summary.card_id``).
        A missing ``started_at`` sorts as the empty string, as the projection does.
        """
        filters: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("status", status),
            ("task_type", task_type),
            ("target_kind", target_kind),
            ("target_id", target_id),
            ("last_failure_class", failure_class),
        ):
            if value is not None:
                filters.append(f"t.{column} = ?")
                params.append(value)
        if chain_id is not None:
            filters.append("json_extract(t.artifacts_summary, '$.chain_id') = ?")
            params.append(chain_id)
        if source_id is not None:
            filters.append(
                "((t.target_kind = 'source' and t.target_id = ?)"
                " or (not (t.target_kind = 'source' and t.target_id != '')"
                " and exists (select 1 from cards c where c.source_id = ? and c.card_id ="
                " case when t.target_kind = 'card' and t.target_id != '' then t.target_id"
                " else json_extract(t.artifacts_summary, '$.card_id') end)))"
            )
            params.extend((source_id, source_id))
        if started_from is not None:
            filters.append("coalesce(t.started_at, '') >= ?")
            params.append(started_from)
        if started_to is not None:
            filters.append("coalesce(t.started_at, '') <= ?")
            params.append(started_to)
        where = f" where {' and '.join(filters)}" if filters else ""
        sql = f"select t.* from tasks t{where} order by t.created_at desc, t.task_id limit ?"
        params.append(int(limit))
        with connect_existing(self.db_path) as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_task(row) for row in rows]

    def iter_task_ids(self, *, statuses: tuple[str, ...] | None = None, exclude: bool = False):
        """Stream task identities only, optionally limited to (or excluding) statuses."""
        sql = "select task_id from tasks"
        params: tuple[str, ...] = ()
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" where status {'not in' if exclude else 'in'} ({placeholders})"
            params = statuses
        with connect_existing(self.db_path) as conn:
            for row in conn.execute(sql, params):
                yield str(row["task_id"])

    def list_by_target(self, *, target_kind: str, target_id: str) -> list[Task]:
        with connect_existing(self.db_path) as conn:
            rows = conn.execute(
                "select * from tasks where target_kind = ? and target_id = ? order by next_run_at, created_at, task_id",
                (target_kind, target_id),
            ).fetchall()
        return [_row_to_task(row) for row in rows]


def _row_to_task(row: Any) -> Task:
    dependencies = json.loads(row["depends_on"])
    artifacts = json.loads(row["artifacts_summary"])
    return Task(
        task_id=row["task_id"],
        task_type=TaskType(row["task_type"]),
        target_kind=row["target_kind"],
        target_id=row["target_id"],
        status=TaskStatus(row["status"]),
        depends_on=tuple(
            DependencySpec(task_id=item["task_id"], required_status=TaskStatus(item["required_status"]))
            for item in dependencies
        ),
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        next_run_at=row["next_run_at"],
        error_reason=row["error_reason"],
        artifacts_summary=artifacts,
        claimed_by=row["claimed_by"],
        lease_expires_at=row["lease_expires_at"],
        heartbeat_at=row["heartbeat_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        input_version=row["input_version"],
        result_version=row["result_version"],
        last_failure_class=row["last_failure_class"],
        completion_token=row["completion_token"],
        queue_name=row["queue_name"],
        priority=row["priority"],
        deadline_at=row["deadline_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
