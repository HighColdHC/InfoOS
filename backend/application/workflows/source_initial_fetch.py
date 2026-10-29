"""Shared Initial Fetch enqueue and legacy/new task arbitration."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.application.tasks.planner import plan_initial_fetch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.domain.tasks import is_task_cancelled
from backend.domain.sources.capability import resolve_source_capability
from backend.infrastructure.storage.database import connect_writable
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.tasks import operations as task_storage

class SourceTaskSupersededError(RuntimeError):
    def __init__(self, winner_task_id: str):
        super().__init__("superseded")
        self.winner_task_id = winner_task_id


def enqueue_or_reuse_initial_fetch(
    base_dir: Path | str,
    source: dict[str, Any],
    *,
    now: str,
    reason: str,
    retry_terminal: bool = False,
    recovery_key: str | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Transactionally reuse legacy work or enqueue one current Initial Fetch."""
    if not source["enabled"]:
        return _none_result()
    capability = resolve_source_capability(source)
    if capability.status in {"planned", "unsupported"}:
        return _none_result()
    if source["connector_status"] != "implemented_uncalibrated":
        return _none_result()
    planned = plan_initial_fetch(source, now=now)
    if planned is None:
        return _none_result()

    conn = connection or connect_writable(resolve_sqlite_path(base_dir))
    try:
        if connection is None:
            conn.execute("begin immediate")
        active = task_storage.active_initial_version_tasks(conn, planned.target_id, planned.input_version)
        active_initial = next((row for row in active if _is_initial(row)), None)
        active_legacy = next((row for row in active if row["task_type"] == TaskType.SOURCE_CALIBRATE.value), None)
        created = False
        recovered = False
        if active_initial is None and active_legacy is None:
            existing_initial = task_storage.task_row(conn, planned.task_id)
            if existing_initial is None:
                TaskRepository(str(resolve_sqlite_path(base_dir))).enqueue_many([planned], conn=conn)
                created = True
            elif retry_terminal:
                terminal_statuses = {
                    TaskStatus.FAILED.value,
                    TaskStatus.BLOCKED.value,
                }
                if recovery_key is None:
                    terminal_statuses.add(TaskStatus.SKIPPED.value)
                artifacts = json.loads(existing_initial["artifacts_summary"] or "{}")
                recovery_keys = list(artifacts.get("terminal_recovery_keys") or [])
                should_recover = (
                    existing_initial["status"] in terminal_statuses
                    and (recovery_key is None or recovery_key not in recovery_keys)
                )
                if should_recover:
                    if recovery_key is not None:
                        recovery_keys.append(recovery_key)
                        artifacts["terminal_recovery_keys"] = recovery_keys
                    recovered = task_storage.recover_initial_task(
                        conn, planned.task_id, now=now, artifacts=artifacts,
                    )

        winner = task_storage.arbitrate_initial_tasks(conn, planned.target_id, planned.input_version)
        if connection is None:
            conn.commit()
    except Exception:
        if connection is None and conn.in_transaction:
            conn.rollback()
        raise
    finally:
        if connection is None:
            conn.close()

    if winner is None:
        return _none_result()
    return {
        "task_id": str(winner["task_id"]),
        "task_type": str(winner["task_type"]),
        "initial_fetch": _is_initial(winner),
        "created": created and str(winner["task_id"]) == planned.task_id,
        "recovered": recovered and str(winner["task_id"]) == planned.task_id,
        "reason": reason,
    }


def arbitrate_source_version_tasks(
    base_dir: Path | str,
    *,
    source_id: str,
    input_version: str,
) -> str | None:
    conn = connect_writable(resolve_sqlite_path(base_dir))
    try:
        conn.execute("begin immediate")
        winner = task_storage.arbitrate_initial_tasks(conn, source_id, input_version)
        conn.commit()
        return str(winner["task_id"]) if winner is not None else None
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


def ensure_task_wins_source_arbitration(base_dir: Path | str, task: Any) -> None:
    winner = arbitrate_source_version_tasks(
        base_dir,
        source_id=task.target_id,
        input_version=task.input_version,
    )
    if winner is not None and winner != task.task_id:
        raise SourceTaskSupersededError(winner)


def source_needs_initial_work(
    repo: TaskRepository,
    source: dict[str, Any],
    *,
    recovery_key: str,
) -> bool:
    from backend.application.tasks.input_versions import source_calibration_input_version

    version = source_calibration_input_version(source)
    tasks = repo.list_by_target(target_kind="source", target_id=str(source["source_id"]))
    for task in tasks:
        if task.input_version != version:
            continue
        if task.task_type == TaskType.FETCH and task.artifacts_summary.get("initial_fetch") is True:
            if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.SUCCESS}:
                return False
            if task.status in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
                recovery_keys = task.artifacts_summary.get("terminal_recovery_keys") or []
                return recovery_key not in recovery_keys
            return False
        if task.task_type == TaskType.SOURCE_CALIBRATE:
            if is_task_cancelled(task) or task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
                return False
    return True


def _is_initial(row: Any) -> bool:
    if str(row["task_type"]) != TaskType.FETCH.value:
        return False
    return json.loads(row["artifacts_summary"] or "{}").get("initial_fetch") is True


def _none_result() -> dict[str, Any]:
    return {
        "task_id": None,
        "task_type": None,
        "initial_fetch": False,
        "created": False,
        "recovered": False,
    }
