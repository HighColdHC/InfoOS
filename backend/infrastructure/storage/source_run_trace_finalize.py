"""Self-contained task graph query with explicit reused-finalizer references."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar, TypedDict

class GraphMetadataRow(TypedDict):
    root_id: str
    graph_depth: int
    graph_rank: int


_Row = TypeVar("_Row", bound=GraphMetadataRow)


def load_task_graph_rows(
    conn: sqlite3.Connection,
    root_ids: list[str],
    *,
    columns: str,
    decode: Callable[[sqlite3.Row], _Row],
    graph_depth_limit: int,
    graph_node_limit: int,
    expansion_limit: int | None,
) -> list[_Row]:
    """Load one bounded graph batch, including explicitly referenced reuse.

    A terminal parent persists ``downstream_task_ids`` in the same transaction
    that completes it. When its deterministic finalizer ID already belongs to
    another chain, that durable edge is reuse evidence, not execution evidence
    for the current run. No mutable Card projection participates in the query.
    """

    placeholders = ",".join("?" for _ in root_ids)
    sql = f"""
        with recursive lineage(
          root_id, task_id, chain_id, depth, path, association_hint
        ) as (
          select t.task_id, t.task_id, json_extract(t.artifacts_summary, '$.chain_id'),
                 0, '|' || t.task_id || '|', 'associated_with_this_run'
          from tasks t where t.task_id in ({placeholders}) and t.task_type = 'card_build'
          union all
          select lineage.root_id, child.task_id,
                 json_extract(child.artifacts_summary, '$.chain_id'), lineage.depth + 1,
                 lineage.path || child.task_id || '|',
                 case
                   when json_extract(child.artifacts_summary, '$.chain_id') = lineage.chain_id
                     then 'associated_with_this_run'
                   else 'referenced_existing_pipeline'
                 end
          from lineage
          join tasks parent on parent.task_id = lineage.task_id
          join json_each(json_extract(parent.artifacts_summary, '$.downstream_task_ids')) next
          join tasks child on child.task_id = next.value
          where lineage.association_hint != 'referenced_existing_pipeline'
            and lineage.chain_id is not null and lineage.chain_id != ''
            and (
              (
                json_extract(child.artifacts_summary, '$.chain_id') = lineage.chain_id
                and (exists (
                  select 1 from json_each(child.depends_on) dep
                  where json_extract(dep.value, '$.task_id') = lineage.task_id
                ) or (child.task_type = 'card_finalize' and exists (
                  select 1
                  from json_each(
                    json_extract(child.artifacts_summary, '$.upstream_media_tasks')
                  ) upstream
                  where json_extract(upstream.value, '$.task_id') = lineage.task_id
                )))
              )
              or (
                child.task_type = 'card_finalize'
                and json_extract(child.artifacts_summary, '$.chain_id') != lineage.chain_id
                and child.target_kind = 'card'
                and child.target_id = coalesce(
                  json_extract(parent.artifacts_summary, '$.card_id'), parent.target_id
                )
              )
            )
            and lineage.depth < ?
            and instr(lineage.path, '|' || child.task_id || '|') = 0
          limit ?
        ), ranked as (
          select lineage.*,
                 row_number() over (partition by root_id order by depth, task_id) as graph_rank
          from lineage
        )
        select ranked.root_id, ranked.depth as graph_depth, ranked.graph_rank,
               ranked.association_hint, {columns}
        from ranked join tasks t on t.task_id = ranked.task_id
        where ranked.graph_rank <= ?
        order by ranked.root_id, ranked.depth, t.created_at, t.task_id
    """
    params = [
        *root_ids,
        graph_depth_limit + 1,
        min(len(root_ids) * (graph_node_limit + 1), expansion_limit or 10**9),
        graph_node_limit + 1,
    ]
    return [decode(row) for row in conn.execute(sql, tuple(params)).fetchall()]
