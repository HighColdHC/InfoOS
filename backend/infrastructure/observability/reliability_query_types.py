"""SQLite rows and reliability report shapes owned by reliability_query."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import NotRequired, TypedDict

from backend.domain.json_types import JsonObject


class TaskRow(TypedDict):
    _rowid: int
    task_id: str
    task_type: str
    target_kind: str
    target_id: str
    status: str
    depends_on: str
    attempt: int
    max_attempts: int
    next_run_at: str | None
    error_reason: str | None
    artifacts_summary: str
    claimed_by: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    started_at: str | None
    finished_at: str | None
    input_version: str
    result_version: str | None
    last_failure_class: str | None
    completion_token: str | None
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    created_at: str
    updated_at: str
    artifacts: NotRequired[JsonObject]


class TaskArtifacts(TypedDict):
    initial_fetch: bool
    no_data: bool
    download_status: object
    gap_reasons: Iterable[object]


class SourcePlatformRow(TypedDict):
    source_id: str
    platform: str


class QueueTotalRow(TypedDict):
    status: str
    n: int


class SourceRow(TypedDict):
    source_id: str
    source_scope: str
    platform: str
    source_type: str
    identifier: str
    display_name: str
    default_domain_id: str | None
    cadence_hours: float
    notes: str | None
    enabled: int
    connector_status: str
    connector_id: str | None
    raw_metadata: str
    calibration_input_version: str | None
    next_run_at: str | None
    last_run_at: str | None
    created_at: str
    updated_at: str


class RunRow(TypedDict):
    run_id: str
    source_id: str
    connector_id: str | None
    scheduled_for: str
    started_at: str
    finished_at: str
    status: str
    fetched_count: int
    visible_count: int
    new_count: int
    skipped_count: int
    dedup_skipped_count: int
    error_count: int
    duration_ms: int
    next_run_at: str | None
    attempt: int
    trigger: str
    decision: str | None
    error_summary: str | None
    raw_payload: object
    task_type: str
    target_kind: str | None
    target_id: str | None
    artifacts_summary: str
    derived_task_ids: str
    platform: str


class CalibrationRow(TypedDict):
    calibration_id: str
    source_id: str
    platform: str
    source_type: str
    identifier: str
    result: str
    visible_count: int
    sample_count: int
    failure_class: str | None
    error_summary: str | None
    task_id: str
    attempt: int
    calibrated_at: str
    raw_payload: object


class EventPayload(TypedDict):
    source_ids: Iterable[str]


class EventSqlRow(TypedDict):
    event_id: str
    task_id: str | None
    chain_id: str | None
    event_type: str
    status: str | None
    failure_class: str | None
    payload: str
    occurred_at: str


class EventRow(TypedDict):
    event_id: str
    task_id: str | None
    chain_id: str | None
    event_type: str
    status: str | None
    failure_class: str | None
    payload: EventPayload
    occurred_at: str


class QueueSummary(TypedDict):
    pending: int
    running: int
    blocked: int
    failed: int
    oldest_pending_seconds: float | None
    oldest_running_seconds: float | None


class Leases(TypedDict):
    expired: int


class RetrySummary(TypedDict):
    scheduled: int
    lease_recovered: int
    lease_lost: int


class AcquisitionCounts(TypedDict):
    runs_24h: int
    successes_24h: int
    runs_7d: int
    successes_7d: int
    failure_classes: Counter[str]


class AcquisitionSummary(TypedDict):
    runs_24h: int
    success_rate_24h: float | None
    runs_7d: int
    success_rate_7d: float | None
    failure_classes: dict[str, int]


class InitialFetchSummary(TypedDict):
    eligible: int
    grandfathered: int
    accounted: int
    pending: int
    running: int
    success: int
    blocked: int
    failed: int
    success_rate: float | None
    no_data_success: int
    failure_classes_by_platform: dict[str, dict[str, int]]


class ProductYield(TypedDict):
    successful_fetch_raw_items: int
    cards_from_successful_fetch: int
    fetch_to_card_yield: float | None


class Degradation(TypedDict):
    media: int


class Phase4Metrics(TypedDict):
    initial_fetch: InitialFetchSummary
    product_yield: ProductYield
    degradation: Degradation


class CalibrationEvidence(TypedDict):
    total: int
    success: int
    failed: int
    success_rate: float | None


class CalibrationQueue(TypedDict):
    pending: int
    running: int
    blocked: int
    failed: int
    success: int
    auth_required: int
    oldest_pending_seconds: float | None


class CalibrationSummary(TypedDict):
    evidence: CalibrationEvidence
    queue: CalibrationQueue
    by_platform: dict[str, dict[str, int]]


class PipelineSummary(TypedDict):
    fetch_to_card_p50_seconds: float | None
    fetch_to_card_p95_seconds: float | None
    stage_counts: dict[str, int]


class CardChainCounts(TypedDict):
    successful_source_runs: int
    raw_items_from_successful_runs: int
    cards_from_successful_runs: int
    raw_to_card_yield: float | None
    cards: int


class Thresholds(TypedDict):
    public_min_success_rate: float
    browser_min_success_rate: float
    max_expired_leases: int
    max_running_lease_periods: int


class Violation(TypedDict):
    metric: str
    actual: int
    maximum: int


class ReliabilityStatus(TypedDict):
    generated_at: str
    history_window_start: str
    history_window_days: int
    queue: QueueSummary
    leases: Leases
    acquisition: dict[str, AcquisitionSummary]
    calibration: CalibrationSummary
    initial_fetch: InitialFetchSummary
    product_yield: ProductYield
    degradation: Degradation
    browser: dict[str, int]
    retries: RetrySummary
    pipeline: PipelineSummary
    intelligence: CardChainCounts
    thresholds: Thresholds
    violations: list[Violation]
