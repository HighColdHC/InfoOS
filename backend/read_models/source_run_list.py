"""Paginated Source Run list projection for the collector task page."""

from __future__ import annotations

from backend.domain.timestamps import parse_utc_or_none

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict

from backend.infrastructure.storage.source_run_list_queries_types import CatalogRow, CatalogSummary
from backend.read_models.source_run_pipeline import Pipeline, PipelineIssue, TrackingProjection
from backend.read_models.source_run_trace_types import SourceRunSummary, SourceRunWithPipeline
from backend.read_models.task_queue_tracking import QueueHealth

from backend.domain.sources.registry import source_task_view
from backend.infrastructure.storage.source_run_list_queries import load_source_run_catalog_page
from backend.infrastructure.storage.source_run_trace_queries import SourceRunTraceQueries
from backend.read_models.source_run_pipeline import project_list_pipelines, project_planned_source_run
from backend.read_models.source_run_trace import project_source_run_summary
from backend.read_models.task_queue_tracking import queue_summary
from backend.read_models._connections import shared_read_connection


class SelectedRun(SourceRunWithPipeline):
    catalog_product_status: str


class FetchResult(TypedDict):
    total_links: int
    dedup_skipped: int
    pending_items: int
    raw_item_count: int


class SourceRunItem(SourceRunSummary, TrackingProjection):
    root_task_id: str
    source_name: str
    fetch_result: FetchResult
    duration_ms: int | None
    attempt_count: int
    blocking_reason: str | None
    pipeline: Pipeline
    issues: list[PipelineIssue]
    execution_bucket: str


class SourceRunPage(TypedDict):
    summary: CatalogSummary
    queue_summary: dict[str, QueueHealth]
    items: list[SourceRunItem]
    total: int
    total_exact: bool
    counts_exact: bool
    limit: int
    offset: int
    next_offset: int | None

_TARGET_STATUSES = {"completed", "attention", "running", "pending"}
_EXECUTION_SCOPES = {"all", "executed", "upcoming"}
_TASK_VIEWS = {"all", "collection", "research"}


@shared_read_connection
def read_source_run_page(
    base_dir: Path | str,
    *,
    query: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    execution_scope: str = "all",
    task_view: str = "all",
    time_from: str | None = None,
    time_to: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> SourceRunPage:
    """Apply product status mapping before pagination and summary counts."""

    evaluated_at = datetime.now(timezone.utc)
    evaluated_at_text = evaluated_at.isoformat(timespec="seconds").replace("+00:00", "Z")
    queries = SourceRunTraceQueries(base_dir)
    loaded = load_source_run_catalog_page(
        base_dir,
        now=evaluated_at_text,
        last_24h_start=(evaluated_at - timedelta(hours=24)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        time_from=time_from,
        time_to=time_to,
        query=query,
        platform=platform,
        status=status,
        execution_scope=execution_scope,
        task_view=task_view,
        limit=limit,
        offset=offset,
    )
    selected = loaded["rows"]
    projected = _project_selected_rows(
        queries, selected, evaluated_at=evaluated_at_text
    )
    page = []
    for selected_row, row in zip(selected, projected, strict=True):
        item = _source_run_item(row, execution_bucket=selected_row["execution_bucket"])
        page.append(item)
    return {
        "summary": loaded["summary"],
        "queue_summary": queue_summary(base_dir, evaluated_at=evaluated_at_text),
        "items": page,
        "total": loaded["total"],
        "total_exact": loaded["total_exact"],
        "counts_exact": loaded["counts_exact"],
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if loaded["has_more"] else None,
    }


def valid_source_run_status(value: str) -> bool:
    return value in _TARGET_STATUSES


def valid_execution_scope(value: str) -> bool:
    return value in _EXECUTION_SCOPES


def valid_task_view(value: str) -> bool:
    return value in _TASK_VIEWS


def _project_selected_rows(
    queries: SourceRunTraceQueries,
    rows: list[CatalogRow],
    *,
    evaluated_at: str,
) -> list[SelectedRun]:
    queries.attach_list_batch_ids(rows)
    run_rows = [row for row in rows if row["entry_kind"] == "run"]
    projections = {}
    if run_rows:
        loaded = queries.load_list_pipeline_rows(run_rows)
        projections = project_list_pipelines(run_rows, loaded, evaluated_at=evaluated_at)
        for row in run_rows:
            if source_task_view(
                row["platform"] or "", row["source_type"] or ""
            ) != "research":
                continue
            projections[str(row["task_id"])]["card_progress"] = {
                "total": row["projected_card_total"],
                "complete": row["projected_card_complete"],
                "partial": row["projected_card_partial"],
                "failed": row["projected_card_failed"],
            }
    planned = project_planned_source_run()
    return [
        {**project_source_run_summary(row),
         **(projections[str(row["task_id"])] if row["entry_kind"] == "run" else planned),
         "catalog_product_status": row["product_status"]}
        for row in rows
    ]


def _source_run_item(row: SelectedRun, *, execution_bucket: str) -> SourceRunItem:
    card_progress = row["card_progress"]
    status = row["catalog_product_status"]
    aggregate = row["aggregate"]
    item: SourceRunItem = {
        "queue_name": row["queue_name"],
        "priority": row["priority"],
        "deadline_at": row["deadline_at"],
        "wait_seconds": row["wait_seconds"],
        "overdue": row["overdue"],
        "claimed_by": row["claimed_by"],
        "fetch_task_id": row["fetch_task_id"],
        "source_type": row["source_type"],
        "task_view": row["task_view"],
        "source_display_name": row["source_display_name"],
        "entry_kind": row["entry_kind"],
        "scheduled_at": row["scheduled_at"],
        "execution_status": row["execution_status"],
        "chain_id": row["chain_id"],
        "chain_root": row["chain_root"],
        "batch_ids": row["batch_ids"],
        "batch_ids_total": row["batch_ids_total"],
        "batch_ids_truncated": row["batch_ids_truncated"],
        "commit_run_id": row["commit_run_id"],
        "commit_evidence": row["commit_evidence"],
        "aggregate": row["aggregate"],
        "acquisition_stage": row["acquisition_stage"],
        "enrichment_stage": row["enrichment_stage"],
        "execution_bucket": execution_bucket,
        "source_run_id": row["source_run_id"],
        "root_task_id": row["fetch_task_id"],
        "source_name": row["source_display_name"],
        "platform": row["platform"],
        "status": status,
        "trigger": row["trigger"],
        "source_id": row["source_id"],
        "fetch_result": {
            "total_links": aggregate["visible"],
            "dedup_skipped": aggregate["dedup_skipped"],
            "pending_items": aggregate["new"],
            "raw_item_count": aggregate["fetched"],
        },
        "card_progress": card_progress,
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "duration_ms": _duration_ms(row["started_at"], row["finished_at"]),
        # The summary has never exposed attempt; retain the existing list value.
        "attempt_count": 0,
        "trace_available": row["trace_available"],
        "blocking_reason": _blocking_reason(row),
        "pipeline": row["pipeline"],
        "issues": row["issues"],
    }
    if "acquisition" in row:
        item["acquisition"] = row["acquisition"]
    if "completeness_status" in row:
        item["completeness_status"] = row["completeness_status"]
    if "requested_count" in row:
        item["requested_count"] = row["requested_count"]
    if "observed_count" in row:
        item["observed_count"] = row["observed_count"]
    if "stop_reason" in row:
        item["stop_reason"] = row["stop_reason"]
    return item


def _blocking_reason(row: SelectedRun) -> str | None:
    actionable = row["blocking_reasons"]
    if actionable:
        return actionable[0]
    issue = next(
        (issue for issue in row["issues"] if issue["code"] == "task_blocked"),
        None,
    )
    return issue["code"] if issue else None


def _duration_ms(started: str | None, finished: str | None) -> int | None:
    start = parse_utc_or_none(started)
    finish = parse_utc_or_none(finished)
    return max(0, int((finish - start).total_seconds() * 1000)) if start and finish else None
