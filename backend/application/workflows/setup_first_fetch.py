"""Durable setup-first-fetch task submission workflow."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from backend.application.tasks.planner import (
    now_utc_iso,
    plan_fetch_tasks_for_source,
    pending_due_fetch_task_ids_for_source,
)
from backend.application.usecases.task_submission import submit_task_batch
from backend.application.workflows.source_initial_fetch import enqueue_or_reuse_initial_fetch
from backend.infrastructure.storage.repositories import SourceRepository


from backend.domain.sources.registry import source_browser_platforms


def submit_first_fetch(base_dir: Path) -> dict[str, Any]:
    """Queue setup acquisition; a durable worker owns all connector I/O."""
    source = _first_enabled_browser_source(base_dir)
    if source is None:
        return _failure("no enabled browser-backed source")

    current = now_utc_iso()
    source_id = str(source["source_id"])
    if source["connector_status"] == "implemented_uncalibrated":
        queued = enqueue_or_reuse_initial_fetch(
            base_dir,
            source,
            now=current,
            reason="setup_first_fetch",
            retry_terminal=True,
        )
        task_id = queued["task_id"]
        if not task_id:
            return _failure("no pending initial fetch task could be planned")
        batch = submit_task_batch(
            base_dir,
            batch_type="setup_first_fetch",
            root_tasks=[],
            existing_task_ids=[str(task_id)],
            task_artifact_updates={str(task_id): {"setup_first_fetch": True}},
            source_ids=[source_id],
            metadata={"source_id": source_id, "planned_task_count": 1},
        )
        return {"ok": True, "batch": batch}
    tasks = plan_fetch_tasks_for_source(
        base_dir,
        source,
        now=current,
        horizon_hours=0,
        plan_followup_fetch=False,
        force_now=True,
    )
    tasks = [
        replace(
            task,
            artifacts_summary={**task.artifacts_summary, "setup_first_fetch": True},
        )
        for task in tasks
    ]
    existing_task_ids = (
        []
        if tasks
        else pending_due_fetch_task_ids_for_source(base_dir, source_id, now=current)
    )
    task_ids = [*(task.task_id for task in tasks), *existing_task_ids]
    if not task_ids:
        return _failure("no pending fetch task could be planned")

    batch = submit_task_batch(
        base_dir,
        batch_type="setup_first_fetch",
        root_tasks=tasks,
        existing_task_ids=existing_task_ids,
        task_artifact_updates={
            task_id: {"setup_first_fetch": True}
            for task_id in existing_task_ids
        },
        source_ids=[source_id],
        metadata={"source_id": source_id, "planned_task_count": len(task_ids)},
    )
    return {"ok": True, "batch": batch}


def _first_enabled_browser_source(base_dir: Path) -> dict[str, Any] | None:
    for source in SourceRepository(base_dir).list_sources():
        if not source["enabled"]:
            continue
        if source["platform"].lower() in source_browser_platforms():
            return source
    return None


def _failure(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "errors": [{"message": message}]}
