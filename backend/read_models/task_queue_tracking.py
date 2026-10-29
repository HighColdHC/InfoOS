"""Read-only projections for durable queue ownership and waiting health."""
from __future__ import annotations

from backend.domain.timestamps import format_utc_seconds_z, parse_utc_or_none

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict, cast

from backend.domain.tasks import TASK_QUEUE_NAMES, Task, TaskStatus
from backend.domain.sources.acquisition import AcquisitionArtifacts
from backend.infrastructure.storage.base import loads
from backend.infrastructure.storage.source_run_json import fetch_acquisition, parse_run_artifacts
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


class QueueTracking(TypedDict):
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    wait_seconds: int
    overdue: bool
    claimed_by: str | None


class TaskTracking(QueueTracking, AcquisitionArtifacts):
    pass


class TrackingInput(TypedDict):
    status: str
    created_at: str | None
    started_at: str | None
    finished_at: str | None
    updated_at: str | None
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    claimed_by: str | None
    acquisition: AcquisitionArtifacts


class TrackingTotalRow(TypedDict):
    total: int


class TrackingTaskRow(TypedDict):
    task_id: str
    task_type: str
    target_kind: str
    target_id: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str
    queue_name: str
    priority: int | None
    deadline_at: str | None
    claimed_by: str | None
    artifacts_summary: str


class TrackingItem(TaskTracking):
    task_id: str
    task_type: str
    target_kind: str
    target_id: str
    status: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    updated_at: str


class TrackingPage(TypedDict):
    items: list[TrackingItem]
    total: int
    limit: int
    offset: int
    next_offset: int | None


class QueueHealth(TypedDict):
    pending: int
    running: int
    failed: int
    blocked: int
    overdue: int
    oldest_wait_seconds: int
    running_worker_ids: list[str]
    running_worker_count: int


class QueueCountRow(TypedDict):
    queue_name: str
    status: Literal["pending", "running", "failed", "blocked"]
    total: int


class QueueWaitRow(TypedDict):
    queue_name: str
    status: str
    deadline_at: str | None
    created_at: str
    started_at: str | None
    claimed_by: str | None


def task_tracking_page(
    base_dir: Path | str,
    *,
    limit: int = 100,
    offset: int = 0,
    queue_name: str | None = None,
    status: str | None = None,
    evaluated_at: str | None = None,
) -> TrackingPage:
    clauses = ["queue_name is not null"]
    params: list[str] = []
    if queue_name:
        clauses.append("queue_name = ?")
        params.append(queue_name)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = " and ".join(clauses)
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        total_row = conn.execute(
            f"select count(*) as total from tasks where {where}", tuple(params)
        ).fetchone()
        total = cast(TrackingTotalRow, dict(total_row))["total"]
        rows = conn.execute(
            f"""
            select task_id, task_type, target_kind, target_id, status,
                   queue_name, priority, deadline_at, claimed_by,
                   created_at, started_at, finished_at, updated_at, artifacts_summary
            from tasks
            where {where}
            order by
              case status when 'running' then 0 when 'pending' then 1 else 2 end,
              priority, deadline_at, created_at desc, task_id
            limit ? offset ?
            """,
            (*params, max(1, limit), max(0, offset)),
        ).fetchall()
    now_text = format_utc_seconds_z(parse_utc_or_none(evaluated_at) or datetime.now(timezone.utc))
    task_rows = [cast(TrackingTaskRow, dict(row)) for row in rows]
    items: list[TrackingItem] = [
        {
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "target_kind": row["target_kind"],
            "target_id": row["target_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
            **tracking_fields({
                "status": row["status"], "created_at": row["created_at"],
                "started_at": row["started_at"], "finished_at": row["finished_at"],
                "updated_at": row["updated_at"], "queue_name": row["queue_name"],
                "priority": row["priority"], "deadline_at": row["deadline_at"],
                "claimed_by": row["claimed_by"],
                "acquisition": _fetch_acquisition(row),
            }, evaluated_at=now_text),
        }
        for row in task_rows
    ]
    next_offset = offset + len(items) if offset + len(items) < total else None
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
    }


def queue_summary(base_dir: Path | str, *, evaluated_at: str | None = None) -> dict[str, QueueHealth]:
    now = parse_utc_or_none(evaluated_at) or datetime.now(timezone.utc)
    result: dict[str, QueueHealth] = {
        queue: {
            "pending": 0, "running": 0, "failed": 0, "blocked": 0,
            "overdue": 0, "oldest_wait_seconds": 0,
            "running_worker_ids": [], "running_worker_count": 0,
        }
        for queue in TASK_QUEUE_NAMES
    }
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        counts = conn.execute(
            """
            select queue_name, status, count(*) as total
            from tasks
            where queue_name is not null
              and status in ('pending', 'running', 'failed', 'blocked')
            group by queue_name, status
            """
        ).fetchall()
        rows = conn.execute(
            """
            select queue_name, status, deadline_at, created_at, started_at, claimed_by
            from tasks
            where queue_name is not null
              and status in ('pending', 'running')
            """
        ).fetchall()
    workers: dict[str, set[str]] = {queue: set() for queue in TASK_QUEUE_NAMES}
    for count_row in counts:
        count = cast(QueueCountRow, dict(count_row))
        queue = count["queue_name"]
        if queue in result:
            result[queue][count["status"]] = count["total"]
    for wait_row in rows:
        row = cast(QueueWaitRow, dict(wait_row))
        queue = row["queue_name"]
        if queue not in result:
            continue
        status = row["status"]
        tracking = tracking_fields({
            "status": row["status"], "created_at": row["created_at"],
            "started_at": row["started_at"], "finished_at": None, "updated_at": None,
            "queue_name": queue, "priority": None, "deadline_at": row["deadline_at"],
            "claimed_by": row["claimed_by"], "acquisition": {},
        }, evaluated_at=format_utc_seconds_z(now))
        if tracking["overdue"]:
            result[queue]["overdue"] += 1
        if status == TaskStatus.PENDING.value:
            result[queue]["oldest_wait_seconds"] = max(
                result[queue]["oldest_wait_seconds"], tracking["wait_seconds"]
            )
        if status == TaskStatus.RUNNING.value and row["claimed_by"]:
            workers[queue].add(row["claimed_by"])
    for queue, worker_ids in workers.items():
        result[queue]["running_worker_ids"] = sorted(worker_ids)
        result[queue]["running_worker_count"] = len(worker_ids)
    return result


def task_tracking(task: Task, *, evaluated_at: str | None = None) -> QueueTracking:
    return tracking_fields(
        {
            "queue_name": task.queue_name,
            "priority": task.priority,
            "deadline_at": task.deadline_at,
            "created_at": task.created_at,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "updated_at": task.updated_at,
            "status": task.status.value,
            "claimed_by": task.claimed_by,
            "acquisition": {},
        },
        evaluated_at=evaluated_at,
    )


def tracking_fields(row: TrackingInput, *, evaluated_at: str | None = None) -> TaskTracking:
    now = parse_utc_or_none(evaluated_at) or datetime.now(timezone.utc)
    created = parse_utc_or_none(row["created_at"])
    started = parse_utc_or_none(row["started_at"])
    finished = parse_utc_or_none(row["finished_at"])
    updated = parse_utc_or_none(row["updated_at"])
    status = row["status"]
    if status == TaskStatus.PENDING.value:
        wait_end = now
    elif status == TaskStatus.RUNNING.value:
        wait_end = started or now
    else:
        wait_end = started or finished or updated or now
    wait_seconds = max(0, int((wait_end - created).total_seconds())) if created else 0
    deadline = parse_utc_or_none(row["deadline_at"])
    overdue = bool(
        deadline
        and deadline < now
        and status in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}
    )
    return {
        **row["acquisition"],
        "queue_name": row["queue_name"],
        "priority": row["priority"],
        "deadline_at": row["deadline_at"],
        "wait_seconds": wait_seconds,
        "overdue": overdue,
        "claimed_by": row["claimed_by"],
    }


def _fetch_acquisition(row: TrackingTaskRow) -> AcquisitionArtifacts:
    if row["task_type"] != "fetch":
        return {}
    raw: object = loads(row["artifacts_summary"], {})
    if not isinstance(raw, dict):
        return {}
    return fetch_acquisition(parse_run_artifacts(raw))
