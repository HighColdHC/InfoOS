"""SQL row shapes for bounded Source Run trace reads."""
from __future__ import annotations

from typing import TypedDict

from backend.domain.json_types import JsonObject
from backend.domain.sources.acquisition import AcquisitionArtifacts
from backend.infrastructure.storage.source_run_json import RunArtifacts, RunCheckpoint


class SourceRunSummaryRow(TypedDict):
    tracking_acquisition: AcquisitionArtifacts
    task_id: str | None
    stable_id: str
    entry_kind: str
    target_id: str
    source_id: str
    status: str
    execution_status: str
    scheduled_at: str
    trace_available: int
    attempt: int
    claimed_by: str | None
    last_failure_class: str | None
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    execution_event_count: int
    started_at: str | None
    finished_at: str | None
    next_run_at: str | None
    lease_expires_at: str | None
    created_at: str
    updated_at: str | None
    task_type: str | None
    platform: str | None
    source_type: str | None
    display_name: str | None
    cadence_hours: float | None
    identifier: str | None
    run_id: str | None
    commit_started_at: str | None
    commit_finished_at: str | None
    commit_status: str | None
    trigger: str | None
    fetched_count: int | None
    visible_count: int | None
    new_count: int | None
    dedup_skipped_count: int | None
    artifacts: RunArtifacts
    checkpoint: RunCheckpoint
    batch_ids: list[str]
    batch_ids_total: int
    batch_ids_truncated: bool


class TraceHead(SourceRunSummaryRow):
    depends_on: str
    max_attempts: int
    error_reason: str | None
    heartbeat_at: str | None
    input_version: str
    result_version: str | None
    connector_id: str | None
    committed_at: str | None
    artifacts_summary: str
    checkpoint_payload: str | None


class TaskRow(TypedDict):
    updated_at: str | None
    tracking_acquisition: AcquisitionArtifacts
    task_id: str
    task_type: str
    target_kind: str
    target_id: str
    status: str
    attempt: int
    max_attempts: int
    next_run_at: str | None
    error_reason: str | None
    claimed_by: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    started_at: str | None
    finished_at: str | None
    input_version: str
    result_version: str | None
    last_failure_class: str | None
    created_at: str
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    root_id: str
    association_hint: str
    artifacts: RunArtifacts
    depends: list[object]


class GraphTaskRow(TaskRow):
    graph_rank: int
    graph_depth: int
    depends_on: str
    artifacts_summary: str


class TraceEventRow(TypedDict):
    task_id: str | None
    event_type: str
    status: str | None
    failure_class: str | None
    occurred_at: str


class RawItemRow(TypedDict):
    connector_id: str
    raw_item_id: str
    source_url: str
    run_id: str | None


class TraceCardRow(TypedDict):
    card_id: str
    source_url: str
    title: str
    content: str
    summary: str
    source_platform: str
    source_type: str
    source_author: str | None
    published_at: str | None
    captured_at: str
    workflow_status: str


class TraceBlockRow(TypedDict):
    block_id: str
    card_id: str
    position: int
    layer: str
    block_kind: str
    status: str
    body: str
    segments: str
    source_url: str | None
    provenance: str
    raw_payload: str
    created_at: str


class AssetStatusRow(TypedDict):
    asset_id: str
    asset_kind: str
    status: str


class TraceAssetRow(AssetStatusRow):
    card_id: str
    source_url: str
    remote_url: str | None
    local_path: str | None
    mime_type: str | None
    cleanup_state: str
    created_at: str


class InsightRow(TypedDict):
    card_id: str
    input_version: str
    main_point: str
    why_it_matters: str
    status: str
    topic_assignment: str | None
    scores: str
    score_reasons: str
    is_time_sensitive: int
    evidence_quotes: str
    model: str | None
    prompt_version: str | None
    generated_at: str


class PipelineRows(TypedDict):
    graph: list[GraphTaskRow]
    ancestors: dict[str, list[str]]
    ancestor_gaps: set[str]
    root_fetch: dict[str, str]
    omitted_by_fetch: dict[str, int]
    page_truncated: bool
    loaded_row_count: int
    assets: list[AssetStatusRow]


class TracePage(TraceHead):
    tasks: list[TaskRow]
    events: list[TraceEventRow]
    raw_items: list[RawItemRow]
    cards: list[TraceCardRow]
    external_tasks: list[TaskRow]
    root_ancestors: dict[str, list[str]]
    graph_gaps: list[str]
    blocks: list[TraceBlockRow]
    assets: list[TraceAssetRow]
    memberships: list[JsonObject]
    insights: list[InsightRow]
    annotations: list[JsonObject]
    scores: list[JsonObject]
    decisions: list[JsonObject]
    outputs: list[JsonObject]


class FetchRuns(TypedDict):
    rows: list[SourceRunSummaryRow]
    total: int


class BatchRow(TypedDict):
    task_id: str
    batch_id: str | None
    total: int
    created_at: str | None
    row_kind: int


class AncestorRow(TypedDict):
    root_id: str
    task_id: object
    depth: int
    graph_rank: int


class FetchTaskIdentityRow(TypedDict):
    last_rowid: int
    task_rowid: int | None
    task_id: str | None
