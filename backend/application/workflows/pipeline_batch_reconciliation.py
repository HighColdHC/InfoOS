"""Bounded, starvation-free repair of task-batch descendant links."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.application.tasks.batches import attach_task_to_parent_batches
from backend.application.tasks.repository import TaskRepository
from backend.infrastructure.storage.migrations.runner import DatabaseMigrationError
from backend.infrastructure.storage.reconciliation_cursors import ReconciliationCursorRepository
from backend.infrastructure.storage.reconciliation_tasks import ReconciliationTaskQueries


def repair_batch_links(
    repo: TaskRepository,
    base_dir: Path,
    *,
    cursor_repo: ReconciliationCursorRepository,
    limit: int,
) -> tuple[int, int, list[str]]:
    after, upper = _cursor(cursor_repo.get("batch_links"))
    queries = ReconciliationTaskQueries(base_dir)
    if not upper:
        upper = queries.max_parent_task_id()
    parents = queries.list_parent_page(
        after_task_id=after,
        upper_task_id=upper,
        limit=max(0, limit),
    )
    if not parents:
        cursor_repo.set("batch_links", _encoded_cursor("", ""))
        return 0, 0, []
    repaired = 0
    errors: list[str] = []
    for parent in parents:
        downstream_ids = parent["artifacts_summary"].get("downstream_task_ids") or []
        children = [str(task_id) for task_id in downstream_ids if repo.get(str(task_id))]
        if not children:
            continue
        try:
            repaired += attach_task_to_parent_batches(
                base_dir,
                parent_task_id=parent["task_id"],
                child_task_ids=children,
            )
        except (OSError, sqlite3.Error, DatabaseMigrationError) as exc:
            errors.append(f"batch:{parent['task_id']}:{exc}")
    cursor_repo.set("batch_links", _encoded_cursor(parents[-1]["task_id"], upper))
    return repaired, len(parents), errors


def _cursor(value: str | None) -> tuple[str, str]:
    try:
        payload = json.loads(value or "{}")
    except (TypeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    return str(payload.get("after") or ""), str(payload.get("upper") or "")


def _encoded_cursor(after: str, upper: str) -> str:
    return json.dumps({"after": after, "upper": upper}, separators=(",", ":"))
