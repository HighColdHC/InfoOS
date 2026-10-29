from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from backend.application.usecases.task_submission import submit_task_batch
from backend.application.tasks.planner import (
    now_utc_iso,
    plan_fetch_tasks_for_enabled_sources,
    plan_manual_fetch_task_for_source,
    pending_due_fetch_task_ids_for_enabled_sources,
)
from backend.application.tasks.types import Task
from backend.application.tasks.repository import TaskRepository
from backend.infrastructure.storage.repositories import SourceRepository
from backend.application.workflows.source_initial_fetch import enqueue_or_reuse_initial_fetch


def enqueue_source_batch(
    base_dir: Path | str,
    *,
    source_ids: list[str] | None = None,
    limit: int = 50,
    now: str | None = None,
) -> dict[str, Any]:
    return _submit_source_batch(
        base_dir,
        mode="force",
        source_ids=source_ids,
        limit=limit,
        now=now,
        batch_type="source_run",
    )


def _submit_source_batch(
    base_dir: Path | str,
    *,
    mode: Literal["force", "due"],
    source_ids: list[str] | None,
    limit: int | None,
    now: str | None,
    batch_type: Literal["run_once", "due_sources", "source_run"],
) -> dict[str, Any]:
    base_path = Path(base_dir)
    current = now or now_utc_iso()
    planned_tasks, existing_task_ids = _plan_sources(
        base_path,
        source_ids=source_ids,
        now=current,
        force=mode == "force",
    )
    requested_ids = _dedupe([*(task.task_id for task in planned_tasks), *existing_task_ids])
    if limit is not None:
        requested_ids = requested_ids[: max(1, int(limit))]
    requested = set(requested_ids)
    roots = [task for task in planned_tasks if task.task_id in requested]
    root_ids = {task.task_id for task in roots}
    existing = [task_id for task_id in requested_ids if task_id not in root_ids]
    metadata: dict[str, Any] = {"planned_task_count": len(requested_ids)}
    if batch_type == "source_run":
        metadata.update(
            {
                "planned_task_ids": requested_ids,
                "limit": max(1, int(limit or 50)),
                "source_count": _source_count(base_path, source_ids),
            }
        )
    return submit_task_batch(
        base_path,
        batch_type=batch_type,
        root_tasks=roots,
        existing_task_ids=existing,
        source_ids=source_ids,
        metadata=metadata,
    )


def main(argv: list[str] | None = None) -> int:
    """Submit FETCH roots as a durable batch without consuming the queue."""
    parser = argparse.ArgumentParser(
        description="Submit enabled InfoOS sources to the durable task queue.",
    )
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    initialize_runtime_storage(Path(args.base_dir))
    try:
        batch = enqueue_source_batch(
            Path(args.base_dir),
            limit=max(1, int(args.limit)),
        )
    except ValueError as exc:
        if str(exc) != "task batch requires at least one root task":
            raise
        print(json.dumps({"status": "no_work", "batch": None}, ensure_ascii=False))
        return 0
    print(
        json.dumps(
            {
                "status": "submitted",
                "batch": batch,
                "consumer": "backend.workers.task_worker",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _plan_sources(
    base_dir: Path,
    *,
    source_ids: list[str] | None,
    now: str,
    force: bool,
) -> tuple[list[Task], list[str]]:
    if source_ids is None:
        tasks = plan_fetch_tasks_for_enabled_sources(
            base_dir,
            now=now,
            horizon_hours=0,
            plan_followup_fetch=False,
            force_now=force,
        )
        if force:
            task_repo = TaskRepository.for_base_dir(base_dir)
            waiting_sources = [source for source in SourceRepository(base_dir).list_sources(include_disabled=False)
                               if task_repo.has_circuit_waiting_fetch(str(source["source_id"]))]
            waiting_ids = {str(source["source_id"]) for source in waiting_sources}
            tasks = [task for task in tasks if task.target_id not in waiting_ids]
            tasks.extend(task for source in waiting_sources
                         if (task := plan_manual_fetch_task_for_source(source, now=now)) is not None)
        new_ids = {task.task_id for task in tasks}
        existing = [
            task_id
            for task_id in pending_due_fetch_task_ids_for_enabled_sources(base_dir, now=now)
            if task_id not in new_ids
        ]
        return tasks, existing
    if not source_ids:
        return [], []
    repo = SourceRepository(base_dir)
    tasks: list[Task] = []
    existing: list[str] = []
    for source_id in source_ids:
        source = repo.get_source(source_id)
        if source:
            if source["connector_status"] == "implemented_uncalibrated":
                queued = enqueue_or_reuse_initial_fetch(
                    base_dir,
                    source,
                    now=now,
                    reason="manual_first_fetch",
                    retry_terminal=True,
                )
                if queued["task_id"]:
                    existing.append(str(queued["task_id"]))
                continue
            planned = plan_manual_fetch_task_for_source(
                source,
                now=now,
            )
            if planned:
                tasks.append(planned)
    return tasks, existing


def _dedupe(task_ids: list[str]) -> list[str]:
    return list(dict.fromkeys(task_ids))


def _source_count(base_dir: Path, source_ids: list[str] | None) -> int:
    if source_ids is not None:
        return len(source_ids)
    return len(SourceRepository(base_dir).list_sources(include_disabled=False))


if __name__ == "__main__":
    raise SystemExit(main())
