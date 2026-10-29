"""Indexed candidate streams for Source Run catalog paging."""

from __future__ import annotations

import heapq
import json
import sqlite3
from typing import Any, TypedDict, cast

from backend.domain.sources.registry import research_source_type_pairs
from backend.infrastructure.storage.source_run_trace_filters import apply_list_filters


class CandidateRow(TypedDict):
    task_id: str | None
    stable_id: str
    entry_kind: str
    source_id: str
    status: str
    execution_status: str
    scheduled_at: str
    sort_group: int
    sort_time: float
    execution_bucket: str
    platform: str | None
    source_type: str | None
    display_name: str | None
    identifier: str | None
    artifacts_summary: str


class CandidateBatch(TypedDict):
    rows: list[CandidateRow]
    has_more: bool
    next_cursor: tuple[int, float, str] | None


def load_candidate_batch(
    conn: sqlite3.Connection,
    *,
    now: str,
    query: str | None,
    platform: str | None,
    time_from: str | None,
    time_to: str | None,
    execution_scope: str,
    task_view: str,
    cursor: tuple[int, float, str] | None,
    limit: int,
    fetch_ids: list[str] | None = None,
) -> CandidateBatch:
    """Merge four independently indexed/small candidate streams."""
    stream_limit = limit + 1
    rows: list[CandidateRow] = []
    for kind in ("active", "planned", "committed", "fallback"):
        sql, params = _stream_sql(
            kind, now=now, query=query, platform=platform, time_from=time_from,
            time_to=time_to, execution_scope=execution_scope, task_view=task_view,
            cursor=cursor, limit=stream_limit, count=False, fetch_ids=fetch_ids,
        )
        rows.extend(cast(CandidateRow, dict(row)) for row in conn.execute(sql, params).fetchall())
    ordered = heapq.nsmallest(stream_limit, rows, key=_sort_key)
    return {
        "rows": ordered[:limit],
        "has_more": len(ordered) > limit,
        "next_cursor": _cursor(ordered[limit - 1]) if len(ordered) >= limit else None,
    }


def _stream_sql(
    kind: str,
    *,
    now: str,
    query: str | None,
    platform: str | None,
    time_from: str | None,
    time_to: str | None,
    execution_scope: str,
    task_view: str,
    cursor: tuple[int, float, str] | None,
    limit: int,
    count: bool,
    fetch_ids: list[str] | None = None,
) -> tuple[str, list[Any]]:
    base_sql, params = _base_stream(kind, now, fetch_ids=fetch_ids)
    filters: list[str] = []
    if time_from is not None:
        filters.append("filter_time >= julianday(?)")
        params.append(time_from)
    if time_to is not None:
        filters.append("filter_time <= julianday(?)")
        params.append(time_to)
    apply_list_filters(filters, params, query=query, platform=platform)
    filters.append(_scope_filter(execution_scope))
    view_sql, view_params = _task_view_filter(task_view)
    filters.append(view_sql)
    params.extend(view_params)
    if cursor is not None:
        cursor_sql, cursor_params = _cursor_filter(kind, cursor)
        if cursor_sql != "1=1":
            filters.append(cursor_sql)
            params.extend(cursor_params)
    expressions = _filter_expressions(kind)
    filters = [
        item.replace("artifacts_summary", expressions["artifacts_summary"])
        .replace("filter_time", expressions["filter_time"])
        .replace("sort_time", expressions["sort_time"])
        .replace("display_name", "s.display_name")
        .replace("source_id", expressions["source_id"])
        .replace("stable_id", expressions["stable_id"])
        .replace("source_type", "s.source_type")
        .replace("platform", "s.platform")
        for item in filters
    ]
    where = " and ".join(f"({item})" for item in filters) or "1 = 1"
    selected_sql = f"{base_sql} and {where}"
    if count:
        sql = f"select count(*) from ({selected_sql})"
    else:
        direction = "asc" if kind in {"active", "planned"} else "desc"
        sql = selected_sql + f" order by sort_time {direction}, stable_id asc limit ?"
        params.append(limit)
    return sql, params


def _filter_expressions(kind: str) -> dict[str, str]:
    fallback = "coalesce(json_extract(t.artifacts_summary,'$.scheduled_for'),t.next_run_at,t.started_at,t.created_at)"
    if kind == "committed":
        scheduled = "r.scheduled_for"
    elif kind == "active":
        scheduled = f"coalesce(r.scheduled_for,{fallback})"
    elif kind == "planned":
        scheduled = "s.next_run_at"
    else:
        scheduled = fallback
    planned = kind == "planned"
    return {
        "sort_time": f"coalesce(julianday({scheduled}),-1.0e20)",
        "filter_time": f"julianday({scheduled})",
        "source_id": "s.source_id" if planned else "t.target_id",
        "stable_id": "('planned:'||s.source_id||':'||s.next_run_at)" if planned else "t.task_id",
        "artifacts_summary": "'{}'" if planned else "t.artifacts_summary",
    }


def _base_stream(
    kind: str, now: str, *, fetch_ids: list[str] | None = None,
) -> tuple[str, list[Any]]:
    task_columns = """
      t.task_id, t.task_id as stable_id, 'run' as entry_kind, t.target_id as source_id,
      t.status, case when t.status in ('blocked','skipped') then 'failed' else t.status end
        as execution_status,
      {scheduled_at} as scheduled_at, {sort_group} as sort_group,
      coalesce(julianday({scheduled_at}), -1.0e20) as sort_time,
      case when t.status = 'running' then 'running'
           when t.started_at is not null or t.status in
             ('success','failed','blocked','skipped','cancelled') then 'executed'
           when julianday({scheduled_at}) > julianday(?) then 'future' else 'due' end
        as execution_bucket,
      s.platform, s.source_type, s.display_name, s.identifier
      ,t.artifacts_summary
    """
    fallback_time = "coalesce(json_extract(t.artifacts_summary,'$.scheduled_for'),t.next_run_at,t.started_at,t.created_at)"
    if kind == "active":
        scheduled = f"coalesce(r.scheduled_for,{fallback_time})"
        return (
            "select " + task_columns.format(scheduled_at=scheduled, sort_group="0") + """
             from tasks t indexed by idx_tasks_status
             left join acquisition_checkpoints c on c.task_id=t.task_id
             left join run_history r on r.run_id=c.run_id
             left join sources s on s.source_id=t.target_id
             where t.task_type='fetch' and t.status in ('pending','running')""",
            [now],
        )
    task_table = "tasks t"
    task_params: list[Any] = [now]
    if fetch_ids is not None:
        task_table = "json_each(?) selected_ids cross join tasks t on t.task_id=selected_ids.value"
        task_params.append(json.dumps(fetch_ids))
    if kind == "committed":
        joins = f"""
             from {task_table}
             cross join acquisition_checkpoints c on c.task_id=t.task_id
             cross join run_history r on r.run_id=c.run_id
        """ if fetch_ids is not None else """
             from run_history r
             cross join acquisition_checkpoints c on c.run_id=r.run_id
             cross join tasks t on t.task_id=c.task_id
        """
        return (
            "select " + task_columns.format(scheduled_at="r.scheduled_for", sort_group="1") + joins + """
             left join sources s on s.source_id=t.target_id
             where t.task_type='fetch' and t.status not in ('pending','running')""",
            task_params,
        )
    if kind == "fallback":
        return (
            "select " + task_columns.format(scheduled_at=fallback_time, sort_group="1") + f"""
             from {task_table}
             left join sources s on s.source_id=t.target_id
             where t.task_type='fetch' and t.status not in ('pending','running')
               and not exists (
                 select 1 from acquisition_checkpoints c
                 join run_history r on r.run_id=c.run_id where c.task_id=t.task_id
               )""",
            task_params,
        )
    if kind != "planned":
        raise ValueError(f"unknown source-run candidate stream: {kind}")
    return (
        """select null as task_id, 'planned:'||s.source_id||':'||s.next_run_at as stable_id,
          'planned' as entry_kind, s.source_id, 'pending' as status,
          'pending' as execution_status, s.next_run_at as scheduled_at, 0 as sort_group,
          coalesce(julianday(s.next_run_at), -1.0e20) as sort_time,
          case when julianday(s.next_run_at)>julianday(?) then 'future' else 'due' end
            as execution_bucket,
          s.platform, s.source_type, s.display_name, s.identifier
          ,'{}' as artifacts_summary
        from sources s where s.enabled=1 and s.next_run_at is not null
          and julianday(s.next_run_at)>=julianday(?)
          and not exists (
            select 1 from tasks t
            left join acquisition_checkpoints c on c.task_id=t.task_id
            left join run_history r on r.run_id=c.run_id
            where t.task_type='fetch' and t.target_id=s.source_id
              and (
                coalesce(r.scheduled_for,json_extract(t.artifacts_summary,'$.scheduled_for'),
                  t.next_run_at,t.started_at,t.created_at)=s.next_run_at
                or julianday(coalesce(r.scheduled_for,
                  json_extract(t.artifacts_summary,'$.scheduled_for'),t.next_run_at,
                  t.started_at,t.created_at))=julianday(s.next_run_at)
              )
          )""",
        [now, now],
    )


def _scope_filter(scope: str) -> str:
    if scope == "all":
        return "1=1"
    if scope == "executed":
        return "execution_bucket in ('executed','running')"
    if scope == "upcoming":
        return "execution_bucket in ('due','future')"
    raise ValueError(f"unsupported execution scope: {scope}")


def _task_view_filter(task_view: str) -> tuple[str, list[str]]:
    pairs = research_source_type_pairs()
    pair_sql = " or ".join(
        "(coalesce(platform,'')=? and coalesce(source_type,'')=?)" for _ in pairs
    ) or "0=1"
    params = [value for pair in pairs for value in pair]
    if task_view == "all":
        return "1=1", []
    if task_view == "research":
        return f"({pair_sql})", params
    if task_view == "collection":
        return f"not ({pair_sql})", params
    raise ValueError(f"unsupported task view: {task_view}")


def _cursor_filter(
    kind: str, cursor: tuple[int, float, str]
) -> tuple[str, list[Any]]:
    group, sort_time, stable_id = cursor
    if kind in {"active", "planned"}:
        if group == 1:
            return "0=1", []
        return (
            "sort_time>=? and (sort_time>? or (sort_time=? and stable_id>?))",
            [sort_time, sort_time, sort_time, stable_id],
        )
    if group == 0:
        return "1=1", []
    return (
        "sort_time<=? and (sort_time<? or (sort_time=? and stable_id>?))",
        [sort_time, sort_time, sort_time, stable_id],
    )


def _sort_key(row: CandidateRow) -> tuple[int, float, str]:
    group, sort_time = row["sort_group"], row["sort_time"]
    return group, sort_time if group == 0 else -sort_time, row["stable_id"]


def _cursor(row: CandidateRow) -> tuple[int, float, str]:
    return row["sort_group"], row["sort_time"], row["stable_id"]
