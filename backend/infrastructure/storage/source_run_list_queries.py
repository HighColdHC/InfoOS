"""SQLite-backed catalog paging for the Source Run list."""
# Bounded candidate paging and its recursive lineage SQL share one contract.

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Literal, cast

from backend.domain.sources.registry import research_source_type_pairs
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.source_run_candidate_queries import load_candidate_batch
from backend.infrastructure.storage.source_run_active_candidates import load_active_fetch_ids
from backend.infrastructure.storage.source_run_scan_batches import status_batch_prefix
from backend.infrastructure.storage.source_run_trace_queries import GRAPH_DEPTH_LIMIT, summary_row
from backend.infrastructure.storage.source_run_candidate_queries import CandidateRow
from backend.infrastructure.storage.source_run_list_queries_types import (
    CatalogBatch, CatalogPage, CatalogRow, CatalogSummary,
)


def load_source_run_catalog_page(
    base_dir: Path | str,
    *,
    now: str,
    last_24h_start: str,
    query: str | None,
    platform: str | None,
    time_from: str | None,
    time_to: str | None,
    execution_scope: str,
    status: str | None,
    limit: int,
    offset: int,
    task_view: str = "all",
    conn: sqlite3.Connection | None = None,
) -> CatalogPage:
    """Load one page from one snapshot; a supplied connection must be in a transaction."""
    if conn is None:
        with connect_existing(resolve_sqlite_path(base_dir)) as owned_conn:
            owned_conn.execute("begin")
            return load_source_run_catalog_page(
                base_dir, now=now, last_24h_start=last_24h_start, query=query,
                platform=platform, time_from=time_from, time_to=time_to,
                execution_scope=execution_scope, status=status, limit=limit, offset=offset,
                task_view=task_view, conn=owned_conn,
            )
    if not conn.in_transaction:
        raise ValueError("source-run catalog connection must have an active read transaction")
    active_filter = status in ("running", "pending")
    fetch_ids = load_active_fetch_ids(conn, status=cast(str, status)) if active_filter else None
    batch_limit = max(32, limit * 2)
    logical_batch_size = batch_limit
    if status is None and offset:
        batch_limit = min(2000, max(batch_limit, offset + limit + 1))
    candidate_cursor: tuple[int, float, str] | None = None
    skipped_matches = 0
    selected: list[CatalogRow] = []
    summary: CatalogSummary = {"last_24h": 0, "completed": 0, "attention": 0, "active": 0}
    matching_seen = 0
    candidate_total = 0
    candidates_seen = 0
    exhausted = False
    summary_complete = True

    while active_filter or len(selected) < limit + 1:
        candidates = load_candidate_batch(
            conn, now=now, query=query, platform=platform, time_from=time_from,
            time_to=time_to, execution_scope=execution_scope, task_view=task_view,
            cursor=candidate_cursor, limit=batch_limit, fetch_ids=fetch_ids,
        )
        candidate_rows = candidates["rows"]
        if not candidate_rows:
            exhausted = True
            break
        candidates_seen += len(candidate_rows)
        if status is None and skipped_matches < offset:
            skip_count = min(offset - skipped_matches, len(candidate_rows))
            skipped_matches += skip_count
            summary_complete = False
            candidate_rows = candidate_rows[skip_count:]
            candidate_cursor = candidates["next_cursor"]
            exhausted = not candidates["has_more"]
            candidate_total = candidates_seen if exhausted else candidates_seen + 1
            if not candidate_rows:
                if exhausted:
                    break
                continue
        loaded = _load_source_run_catalog_batch(
            base_dir, now=now, last_24h_start=last_24h_start,
            selected_candidates=candidate_rows, conn=conn,
        )
        candidate_rank = {
            row["stable_id"]: index for index, row in enumerate(candidate_rows)
        }
        rows = sorted(
            loaded["rows"], key=lambda row: candidate_rank[row["stable_id"]]
        )
        consumed = len(candidate_rows)
        if status is not None and not active_filter:
            rows, loaded["summary"], consumed = status_batch_prefix(
                rows, candidate_rank=candidate_rank, candidate_count=consumed,
                batch_size=logical_batch_size, status=status,
                matches_needed=offset - skipped_matches + limit + 1 - len(selected),
            )
            candidates_seen -= len(candidate_rows) - consumed
        summary_keys: tuple[Literal["last_24h", "completed", "attention", "active"], ...] = ("last_24h", "completed", "attention", "active")
        for key in summary_keys:
            summary[key] += loaded["summary"][key]
        for row in rows:
            row.pop("_scan_last_24h")
        matching_rows = [
            row for row in rows if status is None or row["product_status"] == status
        ]
        matching_seen += len(matching_rows)
        for row in matching_rows:
            if skipped_matches < offset:
                skipped_matches += 1
                continue
            if len(selected) < limit + 1:
                selected.append(row)
            if len(selected) >= limit + 1:
                break
        candidate_cursor = candidates["next_cursor"]
        exhausted = not candidates["has_more"] and consumed == len(candidate_rows)
        candidate_total = candidates_seen if exhausted else candidates_seen + 1
        if exhausted:
            break
        if status is not None:
            # Multiples of the old batch size keep its summary boundaries aligned.
            cap = max(logical_batch_size, (2000 // logical_batch_size) * logical_batch_size)
            batch_limit = min(batch_limit * 2, cap)

    has_more = len(selected) > limit
    page_rows = selected[:limit]
    counts_exact = exhausted and summary_complete and not active_filter
    total = matching_seen if status else candidate_total
    if status and not counts_exact and not active_filter:
        total = max(matching_seen, offset + len(page_rows) + int(has_more))
    return {
        "rows": page_rows,
        "total": total,
        "summary": summary,
        "total_exact": active_filter or counts_exact,
        "counts_exact": counts_exact,
        "has_more": has_more,
    }


def _load_source_run_catalog_batch(
    base_dir: Path | str,
    *,
    now: str,
    last_24h_start: str,
    selected_candidates: list[CandidateRow],
    conn: sqlite3.Connection | None = None,
) -> CatalogBatch:
    """Classify one batch; recursive graph and event work only see its candidates."""
    research_pair_sql, research_pair_params = _research_pair_filter("fetch")
    run_ids = [row["stable_id"] for row in selected_candidates if row["entry_kind"] == "run"]
    planned_ids = [
        row["stable_id"] for row in selected_candidates if row["entry_kind"] == "planned"
    ]
    planned_sources = [
        row["source_id"] for row in selected_candidates if row["entry_kind"] == "planned"
    ]
    run_placeholders = ",".join("?" for _ in run_ids) or "null"
    planned_placeholders = ",".join("?" for _ in planned_ids) or "null"
    source_placeholders = ",".join("?" for _ in planned_sources) or "null"
    params: list[Any] = [*run_ids, *planned_sources, *planned_ids]

    sql = f"""
        with recursive base_fetch as materialized (
          select t.task_id, t.task_id as stable_id, 'run' as entry_kind,
                 t.target_id, t.target_id as source_id, t.status,
                 case when t.status in ('blocked', 'skipped') then 'failed'
                      else t.status end as execution_status,
                 coalesce(r.scheduled_for,
                          json_extract(t.artifacts_summary, '$.scheduled_for'),
                          t.next_run_at, t.started_at, t.created_at) as scheduled_at,
                 1 as trace_available, t.attempt, t.claimed_by, t.last_failure_class,
                 t.queue_name, t.priority, t.deadline_at,
                 null as _execution_event_count,
                 t.started_at, t.finished_at, t.next_run_at, t.lease_expires_at,
                 t.created_at, null as updated_at, null as task_type, t.artifacts_summary,
                 s.platform, s.source_type, s.display_name, s.cadence_hours, s.identifier,
                 c.run_id, c.payload as checkpoint_payload,
                 r.started_at as commit_started_at, r.finished_at as commit_finished_at,
                 r.status as commit_status, r.trigger, r.fetched_count, r.visible_count,
                 r.new_count, r.dedup_skipped_count
          from tasks t
          left join sources s on s.source_id = t.target_id
          left join acquisition_checkpoints c on c.task_id = t.task_id
          left join run_history r on r.run_id = c.run_id
          where t.task_type = 'fetch' and t.task_id in ({run_placeholders})
        ), fetch_roots as (
          select * from base_fetch
        ), planned as (
          select null as task_id,
                 'planned:' || s.source_id || ':' || s.next_run_at as stable_id,
                 'planned' as entry_kind, s.source_id as target_id, s.source_id,
                 'pending' as status, 'pending' as execution_status,
                 s.next_run_at as scheduled_at, 0 as trace_available,
                 0 as attempt, null as claimed_by, null as last_failure_class,
                 null as queue_name, null as priority, null as deadline_at,
                 null as _execution_event_count, null as started_at, null as finished_at,
                 s.next_run_at, null as lease_expires_at, s.created_at, null as updated_at, null as task_type,
                 '{{}}' as artifacts_summary, s.platform, s.source_type, s.display_name,
                 s.cadence_hours, s.identifier, null as run_id,
                 '{{}}' as checkpoint_payload, null as commit_started_at,
                 null as commit_finished_at, null as commit_status, 'planned' as trigger,
                 0 as fetched_count, 0 as visible_count, 0 as new_count,
                 0 as dedup_skipped_count
          from sources s
          where s.source_id in ({source_placeholders})
            and 'planned:' || s.source_id || ':' || s.next_run_at
                in ({planned_placeholders})
        ), merged as (
          select * from fetch_roots union all select * from planned
        ), catalog as materialized (
          select merged.*,
                 case
                   when execution_status = 'running' then 'running'
                   when started_at is not null
                     or execution_status in ('success', 'failed', 'blocked', 'skipped', 'cancelled')
                     then 'executed'
                   when julianday(scheduled_at) > julianday(?) then 'future'
                   else 'due'
                 end as execution_bucket,
                 case when entry_kind = 'planned'
                           or execution_status in ('pending', 'running')
                      then 0 else 1 end as sort_group,
                 coalesce(julianday(scheduled_at), -1.0e20) as sort_time
          from merged
        ), roots(fetch_id, root_id) as materialized (
          select catalog.task_id, cast(root.value as text)
          from catalog, json_each(json_extract(
            case when json_valid(catalog.checkpoint_payload) then catalog.checkpoint_payload else '{{}}' end,
            '$.card_build_task_ids'
          )) root
          where catalog.entry_kind = 'run' and root.value is not null
            and trim(cast(root.value as text)) <> ''
        ), root_candidates(fetch_id, root_id, root_chain_id, fetch_chain_id, is_research) as (
          select roots.fetch_id, roots.root_id,
                 json_extract(root_task.artifacts_summary, '$.chain_id'),
                 json_extract(fetch.artifacts_summary, '$.chain_id'),
                 case when {research_pair_sql} then 1 else 0 end
          from roots
          cross join tasks root_task on root_task.task_id = roots.root_id
          join catalog fetch on fetch.task_id = roots.fetch_id
          where root_task.task_type = 'card_build'
        ), candidate_roots(fetch_id, root_id, is_research) as (
          select fetch_id, root_id, is_research
          from root_candidates
          where root_chain_id is not null
            and (root_chain_id = fetch_chain_id or is_research = 1)
        ), direct_associated(fetch_id, root_id) as (
          select candidate.fetch_id, candidate.root_id
          from candidate_roots candidate
          cross join tasks root_task on root_task.task_id = candidate.root_id
          where exists (
            select 1 from json_each(root_task.depends_on) dep
            where json_extract(dep.value, '$.task_id') = candidate.fetch_id
          )
        ), root_ancestors(fetch_id, root_id, task_id, depth, path) as materialized (
          select candidate.fetch_id, candidate.root_id, candidate.root_id, 0,
                 '|' || candidate.root_id || '|'
          from candidate_roots candidate
          where not exists (
            select 1 from direct_associated direct
            where direct.fetch_id = candidate.fetch_id
              and direct.root_id = candidate.root_id
          )
          union all
          select root_ancestors.fetch_id, root_ancestors.root_id,
                 json_extract(dep.value, '$.task_id'), root_ancestors.depth + 1,
                 root_ancestors.path || json_extract(dep.value, '$.task_id') || '|'
          from root_ancestors
          join tasks task on task.task_id = root_ancestors.task_id
          join json_each(task.depends_on) dep
          where json_extract(dep.value, '$.task_id') is not null
            and root_ancestors.depth < ?
            and instr(root_ancestors.path,
                      '|' || json_extract(dep.value, '$.task_id') || '|') = 0
        ), associated_roots(fetch_id, root_id) as (
          select fetch_id, root_id from direct_associated
          union
          select candidate.fetch_id, candidate.root_id
          from candidate_roots candidate
          where candidate.is_research = 1 or exists (
            select 1 from root_ancestors ancestor
            where ancestor.fetch_id = candidate.fetch_id
              and ancestor.root_id = candidate.root_id
              and ancestor.task_id = candidate.fetch_id
          )
        ), root_presence as (
          select roots.fetch_id, count(distinct roots.root_id) as found_roots
          from roots cross join tasks task on task.task_id = roots.root_id
          where task.task_type = 'card_build'
          group by roots.fetch_id
        ), root_expected as (
          select fetch_id, count(*) as expected_roots
          from roots
          group by fetch_id
        ), lineage(
          fetch_id, root_id, task_id, chain_id, depth, path, association_hint
        ) as (
          select associated_roots.fetch_id, associated_roots.root_id, task.task_id,
                 json_extract(task.artifacts_summary, '$.chain_id'), 0,
                 '|' || task.task_id || '|', 'associated_with_this_run'
          from associated_roots cross join tasks task on task.task_id = associated_roots.root_id
          union all
          select lineage.fetch_id, lineage.root_id, child.task_id,
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
            and (
              (
                json_extract(child.artifacts_summary, '$.chain_id') = lineage.chain_id
                and (exists (
                  select 1 from json_each(child.depends_on) dep
                  where json_extract(dep.value, '$.task_id') = lineage.task_id
                ) or (child.task_type = 'card_finalize' and exists (
                  select 1 from json_each(
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
        ), pipeline_metrics as materialized (
          select lineage.fetch_id,
                 sum(task.status = 'running') as running_count,
                 sum(task.status = 'pending') as pending_count,
                 sum(task.status = 'failed') as failed_count,
                 sum(task.status = 'blocked') as blocked_count,
                 sum(task.status = 'running' and task.lease_expires_at is not null
                     and julianday(task.lease_expires_at) < julianday(?)) as expired_lease_count,
                 sum(task.task_type = 'card_finalize') as finalize_total,
                 sum(task.task_type = 'card_finalize' and task.status = 'success'
                     and json_extract(task.artifacts_summary, '$.completeness_status') = 'complete')
                     as finalize_complete,
                 sum(task.task_type = 'card_finalize' and task.status = 'success'
                     and json_extract(task.artifacts_summary, '$.completeness_status') = 'partial')
                     as finalize_partial,
                 sum(task.task_type = 'card_finalize'
                     and task.status in ('failed', 'blocked')) as finalize_failed
          from lineage cross join tasks task on task.task_id = lineage.task_id
          group by lineage.fetch_id
        ), classified as materialized (
          select catalog.*,
                 case when catalog.entry_kind = 'planned' then 0 else (
                   select count(*) from task_events e where e.task_id = catalog.task_id
                     and e.event_type in ('retry_scheduled', 'completed', 'failed',
                                          'blocked', 'skipped')
                 ) end as execution_event_count,
                 coalesce(expected.expected_roots, 0) as projected_card_total,
                 coalesce(metrics.finalize_complete, 0) as projected_card_complete,
                 coalesce(metrics.finalize_partial, 0) as projected_card_partial,
                 coalesce(metrics.finalize_failed, 0) as projected_card_failed,
                 case
                   when catalog.entry_kind = 'planned' then 'pending'
                   when catalog.status = 'running' or coalesce(metrics.running_count, 0) > 0
                     then 'running'
                   when catalog.status = 'pending' or coalesce(metrics.pending_count, 0) > 0
                     then 'pending'
                   when catalog.status in ('failed', 'blocked', 'skipped', 'cancelled')
                     or catalog.checkpoint_payload is null
                     or not json_valid(catalog.checkpoint_payload)
                     or catalog.commit_status is null
                     or coalesce(metrics.failed_count, 0) > 0
                     or coalesce(metrics.blocked_count, 0) > 0
                     or coalesce(metrics.expired_lease_count, 0) > 0
                     or coalesce(presence.found_roots, 0)
                        < coalesce(expected.expected_roots, 0)
                     or coalesce(metrics.finalize_partial, 0) > 0
                     or json_extract(catalog.checkpoint_payload, '$.completeness_status') = 'partial'
                     or coalesce(metrics.finalize_failed, 0) > 0
                     then 'attention'
                   when coalesce(expected.expected_roots, 0) > 0
                     and metrics.finalize_total = expected.expected_roots
                     and metrics.finalize_complete = expected.expected_roots then 'completed'
                   else 'attention'
                 end as product_status
          from catalog
          left join pipeline_metrics metrics on metrics.fetch_id = catalog.task_id
          left join root_presence presence on presence.fetch_id = catalog.task_id
          left join root_expected expected on expected.fetch_id = catalog.task_id
        ), stats as (
          select
            (select count(*) from classified) as total,
            (select count(*) from catalog) as candidate_count,
            count(*) filter (
              where started_at is not null
                and julianday(started_at) >= julianday(?)
                and julianday(started_at) <= julianday(?)
            ) as last_24h,
            count(*) filter (where product_status = 'completed') as completed,
            count(*) filter (where product_status = 'attention') as attention,
            count(*) filter (where product_status in ('running', 'pending')) as active
          from classified
        ), page as (
          select * from classified
          order by sort_group,
            case when sort_group = 0 then sort_time end asc,
            case when sort_group = 1 then sort_time end desc,
            stable_id asc
        )
        select page.*,
               (page.started_at is not null
                and julianday(page.started_at) >= julianday(?)
                and julianday(page.started_at) <= julianday(?)) as _scan_last_24h,
               stats.total, stats.candidate_count,
               stats.last_24h, stats.completed, stats.attention, stats.active
        from stats left join page on 1 = 1
    """
    params.append(now)
    params.extend(research_pair_params)
    params.extend((GRAPH_DEPTH_LIMIT, GRAPH_DEPTH_LIMIT, now))
    params.extend((last_24h_start, now))
    params.extend((last_24h_start, now))

    if conn is None:
        with connect_existing(resolve_sqlite_path(base_dir)) as owned_conn:
            rows = owned_conn.execute(sql, params).fetchall()
    else:
        rows = conn.execute(sql, params).fetchall()

    first = rows[0]
    summary: CatalogSummary = {
        "last_24h": first["last_24h"],
        "completed": first["completed"],
        "attention": first["attention"],
        "active": first["active"],
    }
    total = first["total"]
    candidate_count = first["candidate_count"]
    selected_rows = [row for row in rows if row["entry_kind"] is not None]
    next_cursor = None
    if selected_rows:
        last = selected_rows[-1]
        next_cursor = (last["sort_group"], last["sort_time"], last["stable_id"])
    omitted = (
        "total", "candidate_count", "last_24h", "completed", "attention", "active",
        "_execution_event_count", "sort_group", "sort_time", "artifacts_summary", "checkpoint_payload",
    )
    page_rows = [cast(CatalogRow, summary_row(row, omit=omitted)) for row in selected_rows]
    return {"rows": page_rows, "total": total, "summary": summary,
            "candidate_count": candidate_count,
            "next_cursor": next_cursor}


def _research_pair_filter(alias: str | None = None) -> tuple[str, list[str]]:
    pairs = research_source_type_pairs()
    if not pairs:
        return "0 = 1", []
    prefix = f"{alias}." if alias else ""
    pair_sql = " or ".join(
        f"(coalesce({prefix}platform, '') = ? "
        f"and coalesce({prefix}source_type, '') = ?)"
        for _ in pairs
    )
    return pair_sql, [value for pair in pairs for value in pair]
