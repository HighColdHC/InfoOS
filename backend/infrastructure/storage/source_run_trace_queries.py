"""Bounded, batch SQLite reads for the Source Run projection."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any, Iterable, cast
from collections.abc import Sequence
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.source_run_trace_filters import apply_list_filters
from backend.infrastructure.storage.source_run_trace_finalize import load_task_graph_rows
from backend.infrastructure.storage.source_run_json import fetch_acquisition, json_list, parse_run_artifacts, parse_run_checkpoint
from backend.infrastructure.storage.source_run_trace_queries_types import (
    AncestorRow, AssetStatusRow, BatchRow, FetchRuns, GraphTaskRow, InsightRow,
    PipelineRows, RawItemRow, FetchTaskIdentityRow, SourceRunSummaryRow, TaskRow, TraceAssetRow,
    TraceBlockRow, TraceCardRow, TraceEventRow, TraceHead, TracePage,
)
GRAPH_NODE_LIMIT = 64
GRAPH_DEPTH_LIMIT = 24
LIST_PAGE_ROOT_LIMIT = 400
LIST_PAGE_NODE_LIMIT = 2000
LIST_RUN_ROOT_LIMIT = 64
BATCH_IDS_PER_FETCH_LIMIT = 10
BATCH_IDS_PAGE_LIMIT = 500
def _rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, tuple(params)).fetchall()


def _in_rows(conn: sqlite3.Connection, sql: str, values: list[str]) -> list[sqlite3.Row]:
    result: list[sqlite3.Row] = []
    for start in range(0, len(values), 400):
        chunk = values[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        result.extend(_rows(conn, sql.format(placeholders=placeholders), chunk))
    return result


def summary_row(row: sqlite3.Row, *, omit: tuple[str, ...] = ()) -> SourceRunSummaryRow:
    artifacts = parse_run_artifacts(row["artifacts_summary"])
    checkpoint = parse_run_checkpoint(row["checkpoint_payload"])
    return cast(SourceRunSummaryRow, {key: row[key] for key in row.keys() if key not in omit} | {
        "artifacts": artifacts,
        "checkpoint": checkpoint,
        "tracking_acquisition": fetch_acquisition(artifacts, checkpoint)
        if row["task_type"] == "fetch" else {},
        "batch_ids": [], "batch_ids_total": 0, "batch_ids_truncated": False,
    })

def _task_row(row: sqlite3.Row) -> TaskRow:
    artifacts = parse_run_artifacts(row["artifacts_summary"])
    return cast(TaskRow, dict(row) | {
        "depends": json_list(row["depends_on"]),
        "artifacts": artifacts,
        "updated_at": None,
        "tracking_acquisition": fetch_acquisition(artifacts)
        if row["task_type"] == "fetch" else {},
    })

class SourceRunTraceQueries:
    """Keeps list and trace query counts independent of page/item size."""

    def __init__(self, base_dir: Path | str):
        self.db_path = resolve_sqlite_path(base_dir)

    def list_fetch_runs(
        self,
        *,
        limit: int,
        offset: int,
        now: str,
        horizon_end: str,
        time_from: str | None = None,
        time_to: str | None = None,
        status: str | None = None,
        query: str | None = None,
        platform: str | None = None,
        include_batches: bool = True,
    ) -> FetchRuns:
        filters = []
        params: list[Any] = [horizon_end, now, horizon_end]
        if time_from is not None:
            filters.append("julianday(scheduled_at) >= julianday(?)")
            params.append(time_from)
        if time_to is not None:
            filters.append("julianday(scheduled_at) <= julianday(?)")
            params.append(time_to)
        if status is not None:
            filters.append("execution_status = ?")
            params.append(status)
        apply_list_filters(filters, params, query=query, platform=platform)
        filter_sql = " and ".join(filters) or "1 = 1"
        with connect_existing(self.db_path) as conn:
            sql_rows = _rows(
                conn,
                f"""
                with base_fetch as (
                  select t.task_id, t.task_id as stable_id, 'run' as entry_kind,
                         t.target_id, t.target_id as source_id, t.status,
                         case when t.status in ('blocked', 'skipped') then 'failed'
                              else t.status end as execution_status,
                         coalesce(r.scheduled_for,
                                  json_extract(t.artifacts_summary, '$.scheduled_for'),
                                  t.next_run_at, t.started_at, t.created_at) as scheduled_at,
                         1 as trace_available, t.attempt, t.claimed_by, t.last_failure_class,
                         t.queue_name, t.priority, t.deadline_at,
                         (select count(*) from task_events e where e.task_id = t.task_id
                          and e.event_type in ('retry_scheduled', 'completed', 'failed',
                                               'blocked', 'skipped')) as execution_event_count,
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
                  where t.task_type = 'fetch'
                ), fetch_roots as (
                  select * from base_fetch
                  where status != 'pending' or julianday(scheduled_at) <= julianday(?)
                ), planned as (
                  select null as task_id,
                         'planned:' || s.source_id || ':' || s.next_run_at as stable_id,
                         'planned' as entry_kind, s.source_id as target_id, s.source_id,
                         'pending' as status, 'pending' as execution_status,
                         s.next_run_at as scheduled_at, 0 as trace_available,
                         0 as attempt, null as claimed_by, null as last_failure_class,
                         null as queue_name, null as priority, null as deadline_at,
                         0 as execution_event_count, null as started_at, null as finished_at,
                         s.next_run_at, null as lease_expires_at, s.created_at, null as updated_at, null as task_type,
                         '{{}}' as artifacts_summary, s.platform, s.source_type, s.display_name,
                         s.cadence_hours, s.identifier, null as run_id,
                         '{{}}' as checkpoint_payload, null as commit_started_at,
                         null as commit_finished_at, null as commit_status, 'planned' as trigger,
                         0 as fetched_count, 0 as visible_count, 0 as new_count,
                         0 as dedup_skipped_count
                  from sources s
                  where s.enabled = 1 and s.next_run_at is not null
                    and julianday(s.next_run_at) >= julianday(?)
                    and julianday(s.next_run_at) <= julianday(?)
                    and not exists (
                      select 1 from base_fetch f
                      where f.source_id = s.source_id
                        and (f.scheduled_at = s.next_run_at
                             or julianday(f.scheduled_at) = julianday(s.next_run_at))
                    )
                ), merged as (
                  select * from fetch_roots union all select * from planned
                ), filtered as (
                  select * from merged where {filter_sql}
                ), page as (
                  select * from filtered
                  order by
                    case when entry_kind = 'planned' or execution_status in ('pending', 'running')
                         then 0 else 1 end,
                    case when entry_kind = 'planned' or execution_status in ('pending', 'running')
                         then julianday(scheduled_at) end asc,
                    case when entry_kind != 'planned' and execution_status not in ('pending', 'running')
                         then julianday(scheduled_at) end desc,
                    stable_id asc
                  limit ? offset ?
                ), totals as (select count(*) as total from filtered)
                select page.*, totals.total from totals left join page on 1 = 1
                """,
                [*params, limit, offset],
            )
            total = sql_rows[0]["total"] if sql_rows else 0
            rows = [summary_row(row, omit=("total", "artifacts_summary", "checkpoint_payload")) for row in sql_rows if row["entry_kind"] is not None]
            ids = [row["task_id"] for row in rows if row["task_id"]]
            batches = (
                self._batch_rows(conn, ids, page_limit=BATCH_IDS_PAGE_LIMIT)
                if include_batches else []
            )
        batch_ids: dict[str, list[str]] = {}
        batch_totals: dict[str, int] = {}
        for batch in batches:
            task_id = batch["task_id"]
            batch_totals[task_id] = batch["total"]
            if batch["batch_id"]:
                batch_ids.setdefault(task_id, []).append(batch["batch_id"])
        for row in rows:
            row["batch_ids"] = batch_ids.get(str(row["task_id"]), [])
            row["batch_ids_total"] = batch_totals.get(str(row["task_id"]), 0)
            row["batch_ids_truncated"] = row["batch_ids_total"] > len(row["batch_ids"])
        return {"rows": rows, "total": total}

    def attach_list_batch_ids(self, rows: Sequence[SourceRunSummaryRow]) -> None:
        """Attach bounded batch identities only to rows selected for the UI page."""
        ids = [row["task_id"] for row in rows if row["task_id"]]
        with connect_existing(self.db_path) as conn:
            batches = self._batch_rows(conn, ids, page_limit=BATCH_IDS_PAGE_LIMIT)
        batch_ids: dict[str, list[str]] = {}
        totals: dict[str, int] = {}
        for batch in batches:
            task_id = batch["task_id"]
            totals[task_id] = batch["total"]
            if batch["batch_id"]:
                batch_ids.setdefault(task_id, []).append(batch["batch_id"])
        for row in rows:
            task_id = str(row["task_id"] or "")
            row["batch_ids"] = batch_ids.get(task_id, [])
            row["batch_ids_total"] = totals.get(task_id, 0)
            row["batch_ids_truncated"] = row["batch_ids_total"] > len(row["batch_ids"])

    def fetch_task_ids_since(self, last_rowid: int) -> tuple[int, list[tuple[int, str]]]:
        """Read new Fetch identities and the task watermark in one snapshot."""
        with connect_existing(self.db_path) as conn:
            rows = conn.execute("""
                with watermark as (select coalesce(max(rowid), 0) as last_rowid from tasks)
                select watermark.last_rowid, t.rowid as task_rowid, t.task_id
                from watermark left join tasks t not indexed
                  on t.rowid > ? and t.rowid <= watermark.last_rowid and t.task_type = 'fetch'
                order by t.rowid
                """, (last_rowid,)).fetchall()
        identities = [cast(FetchTaskIdentityRow, dict(row)) for row in rows]
        return identities[0]["last_rowid"], [
            (cast(int, row["task_rowid"]), row["task_id"])
            for row in identities if row["task_id"] is not None
        ]

    def load_list_pipeline_rows(self, fetch_rows: Sequence[SourceRunSummaryRow]) -> PipelineRows:
        selected_roots: list[str] = []
        omitted_by_fetch: dict[str, int] = {}
        root_fetch: dict[str, str] = {}
        for row in fetch_rows:
            fetch_id = str(row["task_id"])
            roots = [str(value) for value in row["checkpoint"]["card_build_task_ids"] if value]
            allowed = roots[:LIST_RUN_ROOT_LIMIT]
            omitted_by_fetch[fetch_id] = max(0, len(roots) - len(allowed))
            for root_id in allowed:
                if len(selected_roots) >= LIST_PAGE_ROOT_LIMIT:
                    omitted_by_fetch[fetch_id] += 1
                    continue
                selected_roots.append(root_id)
                root_fetch[root_id] = fetch_id
        with connect_existing(self.db_path) as conn:
            graph = self._graph_rows(conn, selected_roots, expansion_limit=LIST_PAGE_NODE_LIMIT + 1)
            ancestors, ancestor_gaps = self._root_ancestors(
                conn, selected_roots, expansion_limit=LIST_PAGE_NODE_LIMIT + 1
            )
            asset_card_ids = sorted({
                row["target_id"]
                for row in graph
                if row["task_type"] == "media_download" and row["target_id"]
            })
            assets = [cast(AssetStatusRow, dict(row)) for row in _in_rows(
                conn,
                "select asset_id, asset_kind, status from card_assets where card_id in ({placeholders})",
                asset_card_ids,
            )]
        page_truncated = len(graph) > LIST_PAGE_NODE_LIMIT
        return {
            "graph": graph[:LIST_PAGE_NODE_LIMIT],
            "ancestors": ancestors,
            "ancestor_gaps": ancestor_gaps,
            "root_fetch": root_fetch,
            "omitted_by_fetch": omitted_by_fetch,
            "page_truncated": page_truncated,
            "loaded_row_count": min(len(graph), LIST_PAGE_NODE_LIMIT),
            "assets": assets,
        }

    def load_trace_head(self, fetch_task_id: str) -> TraceHead | None:
        with connect_existing(self.db_path) as conn:
            head = conn.execute(
                """
                select t.task_id, t.task_id as stable_id, 'run' as entry_kind, t.target_id as source_id, null as updated_at, 0 as execution_event_count, t.task_type, t.target_id, t.status,
                       case when t.status in ('blocked', 'skipped') then 'failed'
                            else t.status end as execution_status,
                       1 as trace_available, t.depends_on,
                       t.attempt, t.max_attempts, t.next_run_at, t.error_reason,
                       t.artifacts_summary, t.claimed_by, t.lease_expires_at,
                       t.queue_name, t.priority, t.deadline_at,
                       t.heartbeat_at, t.started_at, t.finished_at, t.input_version,
                       t.result_version, t.last_failure_class, t.created_at,
                       coalesce(r.scheduled_for, json_extract(t.artifacts_summary, '$.scheduled_for'), t.next_run_at, t.started_at, t.created_at) as scheduled_at,
                       s.platform, s.source_type, s.display_name, s.cadence_hours, s.identifier, s.connector_id,
                       c.run_id, c.committed_at, c.payload as checkpoint_payload,
                       r.started_at as commit_started_at, r.finished_at as commit_finished_at,
                       r.status as commit_status, r.trigger, r.fetched_count, r.visible_count,
                       r.new_count, r.dedup_skipped_count
                from tasks t
                left join sources s on s.source_id = t.target_id
                left join acquisition_checkpoints c on c.task_id = t.task_id
                left join run_history r on r.run_id = c.run_id
                where t.task_id = ? and t.task_type = 'fetch'
                """,
                (fetch_task_id,),
            ).fetchone()
            if head is None:
                return None
            batches = self._batch_rows(conn, [fetch_task_id], page_limit=BATCH_IDS_PER_FETCH_LIMIT)
        result = cast(TraceHead, summary_row(head))
        result["batch_ids"] = [row["batch_id"] for row in batches if row["batch_id"]]
        result["batch_ids_total"] = batches[0]["total"] if batches else 0
        result["batch_ids_truncated"] = result["batch_ids_total"] > len(result["batch_ids"])
        return result
    @staticmethod
    def _batch_rows(conn: sqlite3.Connection, task_ids: list[str], *, page_limit: int) -> list[BatchRow]:
        if not task_ids:
            return []
        placeholders = ",".join("?" for _ in task_ids)
        return [cast(BatchRow, dict(row)) for row in _rows(conn, f"""
            with ranked as (
              select task_id, batch_id, created_at,
                     row_number() over (partition by task_id order by created_at, batch_id) as per_task_rank,
                     count(*) over (partition by task_id) as total
              from task_batch_items where task_id in ({placeholders})
            ), totals as (
              select task_id, max(total) as total from ranked group by task_id
            ), limited_batches as (
              select task_id, batch_id, total, created_at
              from ranked where per_task_rank <= ?
              order by task_id, created_at, batch_id limit ?
            )
            select task_id, null as batch_id, total, null as created_at, 0 as row_kind from totals
            union all
            select task_id, batch_id, total, created_at, 1 as row_kind from limited_batches
            order by task_id, row_kind, created_at, batch_id
            """, [*task_ids, BATCH_IDS_PER_FETCH_LIMIT, page_limit])]

    def load_trace_page(
        self,
        head: TraceHead,
        *,
        root_ids: list[str],
        raw_keys: list[tuple[str, str]],
    ) -> TracePage:
        with connect_existing(self.db_path) as conn:
            loaded_graph = self._graph_rows(conn, root_ids)
            task_rows: list[TaskRow] = []
            graph_gaps_set: set[str] = set()
            for row in loaded_graph:
                rank = row["graph_rank"]
                depth = row["graph_depth"]
                if rank > GRAPH_NODE_LIMIT or depth > GRAPH_DEPTH_LIMIT:
                    graph_gaps_set.add(row["root_id"])
                else:
                    task_rows.append(row)
            graph_gaps = sorted(graph_gaps_set)
            task_ids = [row["task_id"] for row in task_rows]
            event_rows = [cast(TraceEventRow, dict(row)) for row in _in_rows(
                conn,
                "select task_id, event_type, status, failure_class, occurred_at from task_events where task_id in ({placeholders}) order by occurred_at, event_id",
                task_ids,
            )]
            root_ancestors, ancestor_gaps = self._root_ancestors(conn, root_ids)
            graph_gaps = sorted(set(graph_gaps) | ancestor_gaps)
            graph_ids = set(task_ids)
            external_ids = sorted({
                str(dep.get("task_id"))
                for row in task_rows for dep in row["depends"]
                if isinstance(dep, dict) and dep.get("task_id") and dep.get("task_id") not in graph_ids
            })
            external_tasks = [_task_row(row) for row in _in_rows(conn, self._task_refs_sql(), external_ids)]
            raw_rows = self._raw_rows(conn, sorted(set(raw_keys)))
            card_ids = sorted({
                row["artifacts"]["card_id"]
                for row in task_rows if row["artifacts"]["card_id"]
            })
            cards = [cast(TraceCardRow, dict(row)) for row in _in_rows(conn, self._cards_sql(), card_ids)]
            blocks = [cast(TraceBlockRow, dict(row)) for row in _in_rows(conn, self._blocks_sql(), card_ids)]
            assets = [cast(TraceAssetRow, dict(row)) for row in _in_rows(conn, self._assets_sql(), card_ids)]
            insight_keys = sorted({
                (row["target_id"], row["input_version"])
                for row in task_rows if row["task_type"] == "summary" and row["target_kind"] == "card"
            })
            insights = self._insights(conn, insight_keys)
        result: TracePage = {
            **head, "tasks": task_rows, "events": event_rows, "raw_items": raw_rows,
            "cards": cards, "external_tasks": external_tasks, "root_ancestors": root_ancestors,
            "graph_gaps": graph_gaps, "blocks": blocks, "assets": assets,
            "memberships": [], "insights": insights, "annotations": [],
            "scores": [], "decisions": [], "outputs": [],
        }
        return result

    @staticmethod
    def _raw_rows(conn: sqlite3.Connection, keys: list[tuple[str, str]]) -> list[RawItemRow]:
        result: list[RawItemRow] = []
        for start in range(0, len(keys), 200):
            chunk = keys[start:start + 200]
            clauses = " or ".join("(connector_id = ? and raw_item_id = ?)" for _ in chunk)
            params = [value for key in chunk for value in key]
            result.extend(cast(RawItemRow, dict(row)) for row in _rows(conn, f"""select connector_id, raw_item_id, source_url, run_id
                                           from raw_items where {clauses}""", params))
        return result
    @staticmethod
    def _graph_rows(
        conn: sqlite3.Connection, root_ids: list[str], *, expansion_limit: int | None = None
    ) -> list[GraphTaskRow]:
        result: list[GraphTaskRow] = []
        columns = """t.task_id, t.task_type, t.target_kind, t.target_id, t.status, t.depends_on,
                     t.attempt, t.max_attempts, t.next_run_at, t.error_reason, t.artifacts_summary,
                     t.claimed_by, t.lease_expires_at, t.heartbeat_at, t.started_at, t.finished_at,
                     t.input_version, t.result_version, t.last_failure_class,
                     t.queue_name, t.priority, t.deadline_at, t.created_at"""
        for start in range(0, len(root_ids), 400):
            chunk = root_ids[start:start + 400]
            result.extend(load_task_graph_rows(
                conn,
                chunk,
                columns=columns,
                decode=lambda row: cast(GraphTaskRow, _task_row(row)),
                graph_depth_limit=GRAPH_DEPTH_LIMIT,
                graph_node_limit=GRAPH_NODE_LIMIT,
                expansion_limit=expansion_limit,
            ))
        return result

    @staticmethod
    def _root_ancestors(
        conn: sqlite3.Connection, root_ids: list[str], *, expansion_limit: int | None = None
    ) -> tuple[dict[str, list[str]], set[str]]:
        result: dict[str, list[str]] = {}
        gaps: set[str] = set()
        for start in range(0, len(root_ids), 400):
            chunk = root_ids[start:start + 400]
            placeholders = ",".join("?" for _ in chunk)
            rows = [cast(AncestorRow, dict(row)) for row in _rows(conn, f"""
                with recursive ancestors(root_id, task_id, depth, path) as (
                  select task_id, task_id, 0, '|' || task_id || '|'
                  from tasks where task_id in ({placeholders})
                  union
                  select ancestors.root_id, json_extract(dep.value, '$.task_id'), ancestors.depth + 1,
                         ancestors.path || json_extract(dep.value, '$.task_id') || '|'
                  from ancestors join tasks t on t.task_id = ancestors.task_id
                  join json_each(t.depends_on) dep
                  where json_extract(dep.value, '$.task_id') is not null and ancestors.depth < ?
                    and instr(ancestors.path, '|' || json_extract(dep.value, '$.task_id') || '|') = 0
                  limit ?
                ), ranked as (
                  select ancestors.*,
                         row_number() over (partition by root_id order by depth, task_id) as graph_rank
                  from ancestors
                )
                select root_id, task_id, depth, graph_rank from ranked
                where task_id != root_id and graph_rank <= ?
                """, [*chunk, GRAPH_DEPTH_LIMIT + 1,
                       min(len(chunk) * (GRAPH_NODE_LIMIT + 1), expansion_limit or 10**9),
                       GRAPH_NODE_LIMIT + 1])]
            for row in rows:
                root_id = row["root_id"]
                if row["depth"] > GRAPH_DEPTH_LIMIT or row["graph_rank"] > GRAPH_NODE_LIMIT:
                    gaps.add(root_id)
                else:
                    result.setdefault(root_id, []).append(str(row["task_id"]))
        return result, gaps

    @staticmethod
    def _task_refs_sql() -> str:
        return """select task_id, task_type, target_kind, target_id, status, depends_on,
                         attempt, max_attempts, next_run_at, error_reason, artifacts_summary,
                         claimed_by, lease_expires_at, heartbeat_at, started_at, finished_at,
                         input_version, result_version, last_failure_class, created_at,
                         null as queue_name, null as priority, null as deadline_at,
                         '' as root_id, '' as association_hint
                  from tasks where task_id in ({placeholders})"""

    @staticmethod
    def _cards_sql() -> str:
        return """select card_id, source_url, title, content, summary, source_platform,
                         source_type, source_author, published_at, captured_at, workflow_status
                  from cards where card_id in ({placeholders})"""

    @staticmethod
    def _blocks_sql() -> str:
        return """select block_id, card_id, position, layer, block_kind, status, body,
                         segments, source_url, provenance, raw_payload, created_at
                  from card_content_blocks where card_id in ({placeholders})
                  order by card_id, position, created_at, block_id"""

    @staticmethod
    def _assets_sql() -> str:
        return """select asset_id, card_id, asset_kind, source_url, remote_url, local_path,
                         mime_type, status, cleanup_state, created_at
                  from card_assets where card_id in ({placeholders})"""

    @staticmethod
    def _insights(conn: sqlite3.Connection, keys: list[tuple[str, str]]) -> list[InsightRow]:
        if not keys:
            return []
        result: list[InsightRow] = []
        for start in range(0, len(keys), 200):
            chunk = keys[start:start + 200]
            clauses = " or ".join("(card_id = ? and input_version = ?)" for _ in chunk)
            params = [value for key in chunk for value in key]
            result.extend(cast(InsightRow, dict(row)) for row in _rows(conn, f"""select card_id, input_version, main_point, why_it_matters, status,
                                                topic_assignment, scores, score_reasons, is_time_sensitive,
                                                evidence_quotes, model, prompt_version, generated_at
                                         from card_ai_insight_versions where {clauses}""", params))
        return result
