"""Coalesce and stagger platform FETCH recovery in the caller's transaction."""
from __future__ import annotations

from datetime import timedelta
import json
import sqlite3
from typing import Any

from backend.domain.timestamps import format_utc_seconds_z, parse_utc_or_now
from backend.infrastructure.runtime.fetch_rate_policy import recovery_fetch_gap
from backend.infrastructure.storage.task_events import TaskEventRepository


_SOURCE_CIRCUIT_KEY_SQL = """case
    when lower(trim(source.platform)) = 'rss' and source.source_type = 'feed'
      then 'rss:' || source.source_id
    else lower(trim(source.platform)) end"""


def source_has_circuit_waiting_fetch(conn: sqlite3.Connection, source_id: str) -> bool:
    return conn.execute(
        f"""select 1 from tasks task
        where task.target_kind = 'source' and task.target_id = ? and task.task_type = 'fetch'
          and (
            (task.status = 'blocked' and task.last_failure_class in
              ('platform_not_logged_in', 'chrome_not_configured'))
            or (task.status = 'pending' and exists (
              select 1 from sources source join runtime_circuits circuit
                on circuit.scope_kind = 'platform' and circuit.scope_key = {_SOURCE_CIRCUIT_KEY_SQL}
              where source.source_id = task.target_id and circuit.state != 'closed'
            ))
          ) limit 1""",
        (source_id,),
    ).fetchone() is not None


def prepare_platform_fetch_recovery(
    conn: sqlite3.Connection, scope_key: str, *, occurred_at: str,
    incident_id: str | None = None,
) -> list[dict[str, Any]]:
    """Park surviving pending work; return blocked survivors for auth requeue.

    A successful automatic probe has just fetched, so its first peer waits one
    gap. Manual recovery may start its first fetch immediately. Existing future
    deadlines are never pulled forward. Running tasks are never changed.
    """
    rows = conn.execute(
        f"""select task.task_id, task.target_id, task.status, task.created_at,
                  task.next_run_at, task.artifacts_summary, source.raw_metadata,
                  source.platform, source.source_scope,
                  json_extract(task.artifacts_summary, '$.chain_id') as chain_id,
                  exists (select 1 from task_batch_items item join task_batches batch
                    on item.batch_id = batch.batch_id where item.task_id = task.task_id
                    and batch.batch_type in ('source_run', 'run_once')) as manual_submission,
                  exists (select 1 from task_batch_items item join task_batches batch
                    on item.batch_id = batch.batch_id where item.task_id = task.task_id
                    and batch.batch_type in ('research', 'setup_first_fetch', 'capture')) as one_shot_submission
        from tasks task left join sources source
          on task.target_kind = 'source' and task.target_id = source.source_id
        where task.task_type = 'fetch' and task.target_kind = 'source' and (
          (task.status = 'pending' and {_SOURCE_CIRCUIT_KEY_SQL} = ?)
          or (task.status = 'blocked' and task.last_failure_class = 'platform_not_logged_in'
            and exists (select 1 from auth_recovery_incident_tasks linked
              where linked.task_id = task.task_id and linked.incident_id = ?))
        ) order by task.created_at desc, task.rowid desc""",
        (scope_key, incident_id),
    ).fetchall()
    if not rows:
        return []
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        task = dict(row)
        task["artifacts"] = json.loads(task["artifacts_summary"] or "{}")
        artifacts = task["artifacts"]
        if task["manual_submission"]:
            artifacts["manual_run"] = True
        # Different research observations and captured/initial inputs must not
        # replace each other, even if they share a source.
        scope = task["task_id"] if task["one_shot_submission"] or any(artifacts.get(key) for key in (
            "research_batch_id", "capture_id", "initial_fetch", "setup_first_fetch",
        )) else ""
        groups.setdefault((task["target_id"], scope), []).append(task)
    survivors: list[dict[str, Any]] = []
    for tasks in groups.values():
        manual = [task for task in tasks if task["artifacts"].get("manual_run")]
        winner = (manual or tasks)[0]
        survivors.append(winner)
        for loser in tasks:
            if loser["task_id"] != winner["task_id"]:
                _skip_superseded(conn, loser, winner, occurred_at=occurred_at)
    gaps: dict[bool, int] = {}
    for task in survivors:
        family = task["platform"] == "x" and task["source_scope"] == "research"
        task["throttle_family"] = family
        gaps[family] = max(gaps.get(family, 0), recovery_fetch_gap(task))
    slots = {family: parse_utc_or_now(occurred_at) + timedelta(seconds=0 if incident_id else gap)
             for family, gap in gaps.items()}
    blocked: list[dict[str, Any]] = []
    for task in sorted(survivors, key=lambda task: (
        not bool(task["artifacts"].get("manual_run") or task["artifacts"].get("research_batch_id")),
        task["next_run_at"] or occurred_at, task["created_at"], task["task_id"],
    )):
        family = task["throttle_family"]
        gap = gaps[family]
        slot = max(slots[family], parse_utc_or_now(task["next_run_at"] or occurred_at))
        task["next_run_at"] = format_utc_seconds_z(slot)
        task["artifacts"]["platform_recovery_not_before"] = task["next_run_at"]
        conn.execute("update tasks set artifacts_summary = ? where task_id = ?",
                     (json.dumps(task["artifacts"], ensure_ascii=False), task["task_id"]))
        if task["status"] == "blocked":
            blocked.append(task)
        else:
            conn.execute("update tasks set next_run_at = ?, updated_at = ? where task_id = ? and status = 'pending'",
                         (task["next_run_at"], occurred_at, task["task_id"]))
        TaskEventRepository.append_to_connection(
            conn, event_type="recovery_decided", task_id=task["task_id"],
            chain_id=task["chain_id"], status="pending",
            payload={"version": 1, "action": "stagger", "reason": "platform_circuit_recovered",
                     "platform": task["platform"], "next_run_at": task["next_run_at"], "gap_seconds": gap},
            occurred_at=occurred_at,
        )
        slots[family] = slot + timedelta(seconds=gap)
    return blocked


def _skip_superseded(
    conn: sqlite3.Connection, loser: dict[str, Any], winner: dict[str, Any], *, occurred_at: str,
) -> None:
    artifacts = {**loser["artifacts"], "reason": "superseded", "superseded_by": winner["task_id"],
                 "skip_reason": "被同来源更新的任务取代（手动任务优先）"}
    conn.execute(
        """update tasks set status = 'skipped', error_reason = 'superseded',
          artifacts_summary = ?, next_run_at = null, finished_at = ?, updated_at = ?,
          claimed_by = null, lease_expires_at = null, heartbeat_at = null, completion_token = null
        where task_id = ? and status in ('pending', 'blocked')""",
        (json.dumps(artifacts, ensure_ascii=False), occurred_at, occurred_at, loser["task_id"]),
    )
    TaskEventRepository.append_to_connection(
        conn, event_type="skipped", task_id=loser["task_id"], chain_id=loser["chain_id"],
        status="skipped", payload={"version": 1, "reason": "superseded",
                                   "superseded_by": winner["task_id"], "summary": artifacts["skip_reason"]},
        occurred_at=occurred_at,
    )
