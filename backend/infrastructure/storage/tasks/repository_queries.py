"""TaskQueryMixin for durable task storage."""
from __future__ import annotations

from typing import Any, Iterable

from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.infrastructure.storage.tasks.claims import _row_to_task, _task_type_values
from backend.domain.timestamps import utc_now_seconds_z
from backend.domain.task_due_ordering import fair_due_snapshot


class TaskQueryMixin:

    def has_circuit_waiting_fetch(self, source_id: str) -> bool:
        from backend.infrastructure.storage.platform_fetch_recovery import source_has_circuit_waiting_fetch
        with self._connect_existing() as conn:
            return source_has_circuit_waiting_fetch(conn, source_id)

    def get(self, task_id: str) -> Task | None:
        with self._connect_existing() as conn:
            row = conn.execute(
                "select * from tasks where task_id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None


    def list_pending(self) -> list[Task]:
        with self._connect_existing() as conn:
            rows = conn.execute(
                "select * from tasks where status = ? order by created_at",
                (TaskStatus.PENDING.value,),
            ).fetchall()
        return [_row_to_task(r) for r in rows]


    def list_by_target(self, *, target_kind: str, target_id: str, task_type: TaskType | None = None) -> list[Task]:
        clauses = ["target_kind = ?", "target_id = ?"]
        params: list[Any] = [target_kind, target_id]
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type.value)
        sql = "select * from tasks where " + " and ".join(clauses) + " order by next_run_at, created_at, task_id"
        with self._connect_existing() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_task(r) for r in rows]


    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        task_type: TaskType | None = None,
        target_kind: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[Task]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if task_type is not None:
            clauses.append("task_type = ?")
            params.append(task_type.value)
        if target_kind is not None:
            clauses.append("target_kind = ?")
            params.append(target_kind)
        if target_id is not None:
            clauses.append("target_id = ?")
            params.append(target_id)
        where = " where " + " and ".join(clauses) if clauses else ""
        sql = f"select * from tasks{where} order by created_at desc, task_id limit ?"
        params.append(int(limit))
        with self._connect_existing() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_task(row) for row in rows]


    def list_due(
        self, *,
        now: str | None = None,
        limit: int | None = None,
        task_types: Iterable[TaskType | str] | None = None,
        queue_names: Iterable[str] | None = None,
    ) -> list[Task]:
        current = now or utc_now_seconds_z()
        task_type_values = _task_type_values(task_types)
        queue_values = list(queue_names) if queue_names is not None else None
        if task_type_values == [] or queue_values == []:
            return []
        sql = """
            select * from tasks
            where status = ?
              and (next_run_at is null or next_run_at <= ?)
        """
        params: list[Any] = [TaskStatus.PENDING.value, current]
        if task_type_values is not None:
            placeholders = ", ".join("?" for _ in task_type_values)
            sql += f" and task_type in ({placeholders})"
            params.extend(task_type_values)
        if queue_values is not None:
            placeholders = ", ".join("?" for _ in queue_values)
            sql += f" and queue_name in ({placeholders})"
            params.extend(queue_values)
        sql += """
            and not exists (
              select 1 from json_each(tasks.depends_on) dependency
              left join tasks prerequisite
                on prerequisite.task_id = json_extract(dependency.value, '$.task_id')
              where prerequisite.status is null
                 or prerequisite.status != json_extract(dependency.value, '$.required_status')
            )
        """
        sql += (
            " order by next_run_at, created_at, rowid"
            if queue_values is None
            else " order by priority, deadline_at, next_run_at, created_at, rowid"
        )
        if limit is not None:
            sql += " limit ?"
            params.append(max(0, int(limit)) * (10 if queue_values is None else 1))
        with self._connect_existing() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        tasks = [_row_to_task(row) for row in rows]
        if queue_values is None: return fair_due_snapshot(tasks, limit)
        return tasks if limit is None else tasks[:max(0, int(limit))]

    def list_due_page(
        self, *, now: str, queue_names: Iterable[str], limit: int,
        after: tuple[int, str, str, str, int] | None = None,
    ) -> tuple[list[Task], tuple[int, str, str, str, int] | None]:
        """Read one priority-ordered due page without loading an entire queue."""
        queues = list(queue_names)
        if not queues or limit <= 0:
            return [], None
        cursor_key = (
            "coalesce(priority, -1), coalesce(deadline_at, ''), "
            "coalesce(next_run_at, ''), created_at, rowid"
        )
        order_key = "priority, deadline_at, next_run_at, created_at, rowid"
        placeholders = ", ".join("?" for _ in queues)
        sql = f"""select rowid as due_rowid, * from tasks
            where status = 'pending' and (next_run_at is null or next_run_at <= ?)
              and queue_name in ({placeholders})
              and not exists (
                select 1 from json_each(tasks.depends_on) dependency
                left join tasks prerequisite
                  on prerequisite.task_id = json_extract(dependency.value, '$.task_id')
                where prerequisite.status is null
                   or prerequisite.status != json_extract(dependency.value, '$.required_status')
              )"""
        params: list[Any] = [now, *queues]
        if after is not None:
            sql += f" and ({cursor_key}) > (?, ?, ?, ?, ?)"
            params.extend(after)
        sql += f" order by {order_key} limit ?"
        params.append(int(limit))
        with self._connect_existing() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        if not rows:
            return [], None
        last = rows[-1]
        cursor = (
            int(last["priority"] if last["priority"] is not None else -1),
            str(last["deadline_at"] or ""), str(last["next_run_at"] or ""),
            str(last["created_at"]), int(last["due_rowid"]),
        )
        return [_row_to_task(row) for row in rows], cursor
