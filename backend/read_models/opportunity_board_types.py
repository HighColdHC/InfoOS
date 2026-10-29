"""Typed rows and JSON responses for opportunity board projections."""
from typing import NotRequired, TypedDict

from backend.domain.intelligence.opportunity import CandidateKind
from backend.domain.intelligence.opportunity_follow_up import Stage, VerificationFact


class BreakdownFields(TypedDict):
    candidate_kind: CandidateKind | None
    breakdown_id: str
    card_id: str
    created_at: str
    writeback_at: str | None
    evaluator: str
    lens_version: str
    monetization_model: str
    offer: str
    price_signal: str | None
    funnel: str
    target_customer: str | None
    replicability: int
    credibility: int
    next_step: str
    breakdown_note: str
    title: str
    author: str | None
    platform: str
    source_name: str | None
    source_url: str
    published_at: str | None
    author_url: str | None
    is_digest: NotRequired[bool | None]


class RawBreakdownRow(BreakdownFields):
    risk_flags: str
    evidence: str
    verify_questions: str


class BreakdownRow(BreakdownFields):
    risk_flags: list[str]
    evidence: list[str]
    verify_questions: list[str]


class VerificationSummary(VerificationFact):
    credibility: str
    contacted_on: str | None
    doubts: str | None


class VerificationRow(VerificationSummary):
    verification_id: str
    card_id: str
    contact_channel: str
    offer: str
    price: str | None
    delivery: str | None
    note: str | None
    superseded: int


class OpportunityItem(TypedDict):
    candidate_kind: CandidateKind | None
    card_id: str
    title: str
    offer: str
    author: str | None
    platform: str
    source_name: str | None
    source_url: str
    published_at: str | None
    author_url: str | None
    monetization_model: str
    replicability: int
    credibility: int
    next_step: str
    created_at: str
    stage: str | None
    stage_since: str | None
    stale: bool
    archive_reason: str | None
    platform_label: str
    model_label: str
    risk_flags: list[str]
    risk_labels: list[str]
    question_count: int
    verification_count: int
    latest_verification: VerificationSummary | None
    follow_up_reason: str | None


class BoardColumn(TypedDict):
    count: int
    items: list[OpportunityItem]


class BoardToday(TypedDict):
    inbox: int
    plan_stale: int
    contact: int
    action: int


class PlatformLabel(TypedDict):
    platform: str
    label: str


class BoardData(TypedDict):
    columns: dict[str, BoardColumn]
    today: BoardToday
    platforms: list[PlatformLabel]
    models: dict[str, str]


class OpportunityPage(TypedDict):
    items: list[OpportunityItem]
    total: int
    limit: int
    offset: int
    platforms: list[PlatformLabel]
    models: dict[str, str]


class BreakdownHistoryRow(TypedDict):
    breakdown_id: str
    created_at: str
    next_step: str


class TimelineEntry(TypedDict):
    id: str
    kind: str
    created_at: str
    summary: str
    note: str | None


class OpportunityDetail(BreakdownRow, Stage):
    platform_label: str
    labels: dict[str, str]
    risk_labels: list[str]
    timeline: list[TimelineEntry]
    latest_verification: VerificationRow | None
    verification_count: int


class RunRow(TypedDict):
    visible_count: int
    raw_payload: str
    finished_at: str
    platform: str


class FollowUpRow(TypedDict):
    card_id: str
    state: str
    created_at: str


class CandidateRow(TypedDict):
    platform: str
    first_seen_at: str
    current: int


class PlatformCounts(TypedDict):
    visible: int
    matched: int
    broken_down: int
    verify: int


class PlatformStats(PlatformCounts, PlatformLabel):
    pass


class AcquisitionFunnel(PlatformCounts):
    checked: int


class Funnel(AcquisitionFunnel):
    following: int
    contacted: int
    action: int


class DailyCount(TypedDict):
    date: str
    count: int


class BreakdownProgress(TypedDict):
    latest_breakdown_at: str | None
    last_24h: int
    pending: int


class ModelStats(TypedDict):
    model: str
    label: str
    verify: int
    observe: int
    skip: int


class RiskCount(TypedDict):
    risk: str
    label: str
    count: int


class ArchiveCounts(TypedDict):
    dismissed: int
    dropped: int


class FollowUpStats(TypedDict):
    stages: dict[str, int]
    archive: ArchiveCounts
    median_contact_days: float | None


class StatsData(TypedDict):
    window: str
    timezone: str
    funnel: Funnel
    platforms: list[PlatformStats]
    daily: list[DailyCount]
    progress: BreakdownProgress
    next_steps: dict[str, int]
    models: list[ModelStats]
    points: list[list[int | str]]
    risks: list[RiskCount]
    follow_up: FollowUpStats
