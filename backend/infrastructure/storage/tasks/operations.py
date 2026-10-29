"""Task-table operations shared with application-owned transactions."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.domain.tasks import TaskStatus
from backend.domain.timestamps import utc_now_seconds_z


def merge_artifacts(
    conn: sqlite3.Connection, updates: dict[str, dict[str, Any]], *, updated_at: str,
) -> None:
    for task_id, patch in updates.items():
        row = conn.execute(
            "select artifacts_summary from tasks where task_id = ?", (task_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"missing task ids: {task_id}")
        artifacts = json.loads(row["artifacts_summary"])
        artifacts.update(patch)
        conn.execute(
            "update tasks set artifacts_summary = ?, updated_at = ? where task_id = ?",
            (json.dumps(artifacts), updated_at, task_id),
        )


def capture_task_rows(conn: sqlite3.Connection, task_id: str, capture_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """select task_id, status, updated_at from tasks
        where task_id = ? or json_extract(artifacts_summary, '$.capture_id') = ?
        order by created_at, task_id""",
        (task_id, capture_id),
    ).fetchall()


def chain_task_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "select task_id, task_type, target_id, status, artifacts_summary from tasks"
    ).fetchall()


def task_ids_present(conn: sqlite3.Connection, task_ids: list[str]) -> set[str]:
    placeholders = ", ".join("?" for _ in task_ids)
    rows = conn.execute(
        "select task_id from tasks where task_id in (" + placeholders + ")",
        tuple(task_ids),
    ).fetchall()
    return {str(row["task_id"]) for row in rows}


def task_created_at(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, str]:
    placeholders = ", ".join("?" for _ in task_ids)
    rows = conn.execute(
        "select task_id, created_at from tasks where task_id in (" + placeholders + ")",
        tuple(task_ids),
    ).fetchall()
    return {str(row["task_id"]): str(row["created_at"]) for row in rows}


def task_row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute("select * from tasks where task_id = ?", (task_id,)).fetchone()


def active_initial_version_tasks(
    conn: sqlite3.Connection, source_id: str, input_version: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        """select * from tasks
        where target_kind = 'source' and target_id = ? and input_version = ?
          and status in ('pending', 'running')
          and (
            task_type = 'source_calibrate'
            or (task_type = 'fetch' and json_extract(artifacts_summary, '$.initial_fetch') = 1)
          )
        order by created_at, task_id""",
        (source_id, input_version),
    ).fetchall()


def recover_initial_task(
    conn: sqlite3.Connection, task_id: str, *, now: str, artifacts: dict[str, Any],
) -> bool:
    cursor = conn.execute(
        """update tasks set status = 'pending', attempt = 1, next_run_at = ?,
          error_reason = null, artifacts_summary = ?,
          result_version = null, last_failure_class = null,
          claimed_by = null, lease_expires_at = null,
          heartbeat_at = null, completion_token = null,
          started_at = null, finished_at = null, updated_at = ?
        where task_id = ? and status in ('failed', 'blocked', 'skipped')""",
        (now, json.dumps(artifacts, ensure_ascii=False, sort_keys=True), now, task_id),
    )
    return bool(cursor.rowcount)


def arbitrate_initial_tasks(
    conn: sqlite3.Connection, source_id: str, input_version: str,
) -> sqlite3.Row | None:
    active = active_initial_version_tasks(conn, source_id, input_version)
    if not active:
        return None
    winner = min(active, key=lambda row: (str(row["created_at"]), str(row["task_id"])))
    for loser in active:
        if loser["task_id"] == winner["task_id"]:
            continue
        artifacts = json.loads(loser["artifacts_summary"] or "{}")
        artifacts.update({"reason": "superseded", "superseded_by": winner["task_id"]})
        now = utc_now_seconds_z()
        conn.execute(
            """update tasks set status = 'skipped', error_reason = 'superseded', artifacts_summary = ?,
              claimed_by = null, lease_expires_at = null, heartbeat_at = null,
              completion_token = null, finished_at = coalesce(finished_at, ?), updated_at = ?
            where task_id = ? and status in ('pending', 'running')""",
            (json.dumps(artifacts), now, now, loser["task_id"]),
        )
    return winner


def update_admitted_source_version(
    conn: sqlite3.Connection, *, task_id: str, worker_id: str, attempt: int, version: str,
) -> bool:
    row = conn.execute(
        """select artifacts_summary from tasks
        where task_id = ? and status = 'running' and claimed_by = ? and attempt = ?""",
        (task_id, worker_id, attempt),
    ).fetchone()
    if row is None:
        return False
    artifacts = json.loads(row["artifacts_summary"])
    artifacts["source_input_version"] = version
    updated = conn.execute(
        """update tasks set input_version = ?, artifacts_summary = ?
        where task_id = ? and status = 'running' and claimed_by = ? and attempt = ?""",
        (version, json.dumps(artifacts), task_id, worker_id, attempt),
    )
    return updated.rowcount == 1


def update_fetch_checkpoint_artifacts(
    conn: sqlite3.Connection, *, task_id: str, worker_id: str, attempt: int,
    checkpoint: dict[str, Any],
) -> bool:
    row = conn.execute(
        "select artifacts_summary from tasks where task_id = ?", (task_id,),
    ).fetchone()
    current = json.loads(row["artifacts_summary"]) if row else {}
    durable = {**current, **checkpoint, "acquisition_checkpoint": checkpoint}
    updated = conn.execute(
        """update tasks set artifacts_summary = ?
        where task_id = ? and status = ? and claimed_by = ? and attempt = ?""",
        (json.dumps(durable), task_id, TaskStatus.RUNNING.value, worker_id, attempt),
    )
    return bool(updated.rowcount)
