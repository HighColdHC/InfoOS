"""Read-only SQLite queries for task-batch projections."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict, cast

from backend.domain.json_types import JsonObject

from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


class BatchRow(TypedDict):
    batch_id: str
    batch_type: str
    status: str
    source_ids: str
    metadata: str
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None


class BatchItemRow(TypedDict):
    batch_id: str
    task_id: str


class BatchTaskRow(TypedDict):
    task_id: str
    task_type: str
    status: str
    created_at: str
    created_or_updated: str | None


class BatchSummary(TypedDict):
    planned: int
    total: int
    terminal: int
    progress_percent: int
    current_stage: str | None
    oldest_pending_at: str | None
    running: int
    succeeded: int
    failed: int
    blocked: int
    skipped: int
    pending: int
    new_cards: int


class TaskBatch(TypedDict):
    batch_id: str
    batch_type: str
    status: str
    task_ids: list[str]
    source_ids: list[str]
    metadata: JsonObject
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    summary: BatchSummary


def get_task_batch(base_dir: Path | str, batch_id: str) -> TaskBatch:
    batches = _load_batches(Path(base_dir), where="where batch_id = ?", params=[batch_id])
    if not batches:
        raise KeyError(batch_id)
    return batches[0]


def list_task_batches(base_dir: Path | str, *, limit: int = 100) -> list[TaskBatch]:
    return _load_batches(Path(base_dir), where="order by created_at desc limit ?", params=[int(limit)])


def _load_batches(base_dir: Path, *, where: str, params: list[str | int]) -> list[TaskBatch]:
    db_path = resolve_sqlite_path(base_dir)
    with connect_existing(db_path) as conn:
        rows = [cast(BatchRow, dict(row)) for row in conn.execute(f"select * from task_batches {where}", tuple(params)).fetchall()]
        if not rows:
            return []
        batch_ids = [row["batch_id"] for row in rows]
        items: list[BatchItemRow] = []
        for start in range(0, len(batch_ids), 400):
            chunk = batch_ids[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            items.extend(cast(BatchItemRow, dict(row)) for row in conn.execute(
                f"select batch_id, task_id from task_batch_items where batch_id in ({placeholders}) order by rowid",
                chunk,
            ).fetchall())
        task_ids_by_batch: dict[str, list[str]] = {batch_id: [] for batch_id in batch_ids}
        for item in items:
            task_ids_by_batch[item["batch_id"]].append(item["task_id"])
        task_ids = list(dict.fromkeys(item["task_id"] for item in items))
        tasks_by_id: dict[str, BatchTaskRow] = {}
        for start in range(0, len(task_ids), 400):
            chunk = task_ids[start:start + 400]
            task_placeholders = ",".join("?" for _ in chunk)
            task_rows = conn.execute(
                "select task_id, task_type, status, created_at, "
                "json_extract(artifacts_summary, '$.created_or_updated') as created_or_updated "
                f"from tasks where task_id in ({task_placeholders})",
                chunk,
            ).fetchall()
            tasks_by_id.update((task["task_id"], cast(BatchTaskRow, dict(task))) for task in task_rows)
        result: list[TaskBatch] = []
        for row in rows:
            task_ids = task_ids_by_batch[row["batch_id"]]
            tasks = [tasks_by_id[task_id] for task_id in task_ids if task_id in tasks_by_id]
            summary = _summary(task_ids, tasks)
            result.append(
                {
                    "batch_id": row["batch_id"],
                    "batch_type": row["batch_type"],
                    "status": _batch_status(summary),
                    "task_ids": task_ids,
                    "source_ids": json.loads(row["source_ids"]),
                    "metadata": json.loads(row["metadata"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "started_at": row["started_at"],
                    "finished_at": row["finished_at"],
                    "summary": summary,
                }
            )
    return result


def _summary(
    task_ids: list[str],
    tasks: list[BatchTaskRow],
) -> BatchSummary:
    by_id = {task["task_id"]: task for task in tasks}
    terminal_statuses = {"success", "failed", "blocked", "skipped"}
    terminal = sum(1 for task in tasks if task["status"] in terminal_statuses)
    active = next(
        (by_id[task_id] for task_id in task_ids if task_id in by_id and by_id[task_id]["status"] == "running"),
        None,
    ) or next(
        (by_id[task_id] for task_id in task_ids if task_id in by_id and by_id[task_id]["status"] == "pending"),
        None,
    )
    pending_times = [
        task["created_at"]
        for task in tasks
        if task["status"] == "pending"
    ]
    return {
        "planned": len(task_ids),
        "total": len(task_ids),
        "terminal": terminal,
        "progress_percent": int((terminal * 100) / len(task_ids)) if task_ids else 0,
        "current_stage": active["task_type"] if active else None,
        "oldest_pending_at": min(pending_times) if pending_times else None,
        "running": sum(task["status"] == "running" for task in tasks),
        "succeeded": sum(task["status"] == "success" for task in tasks),
        "failed": sum(task["status"] == "failed" for task in tasks),
        "blocked": sum(task["status"] == "blocked" for task in tasks),
        "skipped": sum(task["status"] == "skipped" for task in tasks),
        "pending": sum(task["status"] == "pending" for task in tasks),
        "new_cards": sum(task["task_type"] == "card_build"
                         and task["created_or_updated"] == "created" for task in tasks),
    }


def _batch_status(summary: BatchSummary) -> str:
    if summary["planned"] == 0:
        return "skipped"
    if summary["running"] > 0:
        return "running"
    if summary["pending"] > 0:
        return "queued"
    terminal_failures = summary["failed"] + summary["blocked"]
    terminal_successes = summary["succeeded"] + summary["skipped"]
    if terminal_failures and terminal_successes:
        return "partial"
    if terminal_failures:
        return "failed"
    return "success"
