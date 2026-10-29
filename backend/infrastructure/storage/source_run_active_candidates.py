"""Reverse active task lineage into a superset of fetch candidates."""

from __future__ import annotations

import json
import sqlite3

from backend.infrastructure.storage.source_run_trace_queries import GRAPH_DEPTH_LIMIT


def load_active_fetch_ids(conn: sqlite3.Connection, *, status: str) -> list[str]:
    """Use status/primary-key lookups; classification owns association checks."""
    ancestors = conn.execute("""
        with recursive active as materialized (
          select task_id, status from tasks indexed by idx_tasks_status
          where status in ('running', 'pending')
        ), ancestors(task_id) as (
          select task_id from active where status = ?
          union
          select json_extract(dep.value, '$.task_id')
          from ancestors
          cross join tasks task on task.task_id = ancestors.task_id
          cross join json_each(task.depends_on) dep
          where json_extract(dep.value, '$.task_id') is not null
          union
          select json_extract(upstream.value, '$.task_id')
          from ancestors
          cross join tasks task on task.task_id = ancestors.task_id
          cross join json_each(json_extract(
            task.artifacts_summary, '$.upstream_media_tasks'
          )) upstream
          where task.task_type = 'card_finalize'
            and json_extract(upstream.value, '$.task_id') is not null
        )
        select task.task_id, task.task_type, task.target_kind, task.status
        from ancestors cross join tasks task on task.task_id = ancestors.task_id
    """, (status,)).fetchall()
    if not ancestors:
        return []
    ancestor_ids = json.dumps([row["task_id"] for row in ancestors])
    fetch_ids = {row["task_id"] for row in ancestors if row["task_type"] == "fetch"}
    # Checkpoint membership also covers research roots reused without a fetch dependency.
    fetch_ids.update(row[0] for row in conn.execute("""
        select distinct checkpoint.task_id
        from acquisition_checkpoints checkpoint,
             json_each(json_extract(
               case when json_valid(checkpoint.payload) then checkpoint.payload else '{}' end,
               '$.card_build_task_ids'
             )) root
        where cast(root.value as text) in (select value from json_each(?))
    """, (ancestor_ids,)))
    finalize_ids = [row["task_id"] for row in ancestors
                    if row["task_type"] == "card_finalize" and row["target_kind"] == "card"
                    and row["status"] == status]
    if finalize_ids:
        fetch_ids.update(_referencing_fetch_ids(conn, finalize_ids))
    return sorted(fetch_ids)


def _referencing_fetch_ids(conn: sqlite3.Connection, finalize_ids: list[str]) -> list[str]:
    """Cover cross-chain finalize edges even when dependencies omit their parents.

    Parent card IDs can live only in historical artifacts, without an index. Walk
    checkpoint-rooted references by task primary key instead of JSON-scanning all
    tasks or assuming that a parent's target ID equals its artifact card ID.
    Cross-chain references are terminal in lineage, so only a finalize with the
    requested status needs this probe; its later descendants cannot mark that run.
    """
    rows = conn.execute("""
        with recursive references_from_roots(fetch_id, task_id, depth) as (
          select checkpoint.task_id, cast(root.value as text), 0
          from acquisition_checkpoints checkpoint,
               json_each(json_extract(
                 case when json_valid(checkpoint.payload) then checkpoint.payload else '{}' end,
                 '$.card_build_task_ids'
               )) root
          where root.value is not null and trim(cast(root.value as text)) <> ''
          union
          select reference.fetch_id, child.task_id, reference.depth + 1
          from references_from_roots reference
          cross join tasks parent on parent.task_id = reference.task_id
          cross join json_each(json_extract(
            parent.artifacts_summary, '$.downstream_task_ids'
          )) next
          cross join tasks child on child.task_id = next.value
          where reference.depth < ?
        )
        select distinct fetch_id from references_from_roots
        where task_id in (select value from json_each(?))
    """, (GRAPH_DEPTH_LIMIT, json.dumps(finalize_ids))).fetchall()
    return [row[0] for row in rows]
