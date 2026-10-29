"""Public Source Run trace response shapes."""
from typing import TypedDict

from backend.read_models.source_run_card_progress import CompleteCardProgress
from backend.read_models.source_run_pipeline import PipelineProjection, RetiredSteps
from backend.read_models.source_run_trace_media import CardBuild, Discovery, FinalCard, ItemCapture, MediaItem
from backend.read_models.task_queue_tracking import TaskTracking


class Aggregate(TypedDict):
    fetched: int
    visible: int
    new: int
    dedup_skipped: int


class CommitEvidence(TypedDict):
    status: str
    started_at: str
    finished_at: str


class SourceRunSummary(TaskTracking):
    source_run_id: str
    fetch_task_id: str
    source_id: str
    platform: str
    source_type: str
    task_view: str
    source_display_name: str
    trigger: str
    entry_kind: str
    scheduled_at: str
    trace_available: bool
    execution_status: str
    started_at: str | None
    finished_at: str | None
    status: str
    chain_id: str
    chain_root: str
    batch_ids: list[str]
    batch_ids_total: int
    batch_ids_truncated: bool
    commit_run_id: str
    commit_evidence: CommitEvidence
    aggregate: Aggregate


class SourceRunWithPipeline(SourceRunSummary, PipelineProjection):
    pass


class SourceRuns(TypedDict):
    items: list[SourceRunWithPipeline]
    limit: int
    offset: int
    total: int
    next_offset: int | None
    planned_horizon_end: str


# ``class`` is a response key, so functional TypedDict syntax is required.
SafeError = TypedDict("SafeError", {"class": str, "summary": str, "next_action": str})


class FetchStats(TypedDict):
    total_links: int | None
    dedup_skipped: int | None
    pending_items: int | None
    raw_item_count: int | None


class TraceRun(SourceRunSummary):
    root_task_id: str
    source_name: str
    duration_ms: int
    attempt_count: int
    error: SafeError | None
    max_attempts: int
    retry_at: str | None
    fetch_stats: FetchStats
    card_progress: CompleteCardProgress


class TrackingRow(TaskTracking):
    task_id: str
    task_type: str
    status: str


class ContentEnrich(TypedDict):
    availability: str
    task_id: str
    status: str
    attempt: int | None
    max_attempts: int | None
    retryable: bool
    error: SafeError | None


class Dependency(TypedDict):
    task_id: str
    required_status: object
    actual_status: str
    satisfied: bool
    association: str


class StageInput(TypedDict):
    target_kind: str
    target_id: str
    input_version: str


class Versions(TypedDict):
    input: str
    result: str


class Stage(TypedDict):
    task_id: str
    stage: str
    status: str
    operation: str
    association: str
    execution: str
    dependencies: list[Dependency]
    input: StageInput
    output: None
    product_gap: None
    attempt: int
    max_attempts: int
    retry_at: str | None
    lease_expires_at: str | None
    started_at: str | None
    finished_at: str | None
    duration_seconds: int | None
    error: SafeError | None
    versions: Versions
    downstream_task_ids: list[str]


class TraceItem(ItemCapture):
    connector_id: str
    raw_item_id: str
    card_id: str
    association: str
    execution: str
    card_projection_provenance: str
    current_projection_url: str
    content_enrich: ContentEnrich
    stages: list[Stage]
    media_branches: list[MediaItem]
    final_card: FinalCard | None


class CheckpointEvidence(TypedDict):
    evidence: str
    committed_at: str
    commit_run_id: str
    key_count: int


class TraceAcquisition(TypedDict):
    provider: str
    connector: str
    operation: str
    invocation_evidence: str
    aggregate: Aggregate
    cursor_before: str
    cursor_after: str
    checkpoint: CheckpointEvidence


class ItemPage(TypedDict):
    items: list[TraceItem]
    item_total: int
    truncated: bool
    next_cursor: str | None


class SourceRunTrace(TypedDict):
    source_run_id: str
    events_archived: bool
    events_hot_window_days: int
    run: TraceRun
    discovery: Discovery
    card_build: CardBuild
    retired_steps: RetiredSteps
    items: list[TraceItem]
    task_tracking: list[TrackingRow]
    acquisition: TraceAcquisition
    item_page: ItemPage
    gaps: list[str]
