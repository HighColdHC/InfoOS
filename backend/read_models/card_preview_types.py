"""Public card preview response contracts."""
from typing import NotRequired, TypedDict

from backend.domain.advertising import AdMetadata
from backend.domain.json_types import JsonObject
from backend.infrastructure.storage.card_browse_query_types import PreviewOpportunity


class PreviewEvaluation(TypedDict):
    quality: int
    relevance: int | None
    attribution_kind: str | None
    attribution_ref: str | None
    content_type: str
    suggested_action: str
    summary: str
    relation_note: str
    prompt_version: str
    profile_version: int
    model: str
    passed_quality: bool
    evaluated_at: str


class PreviewItem(TypedDict):
    card_id: str
    title: str
    author: str | None
    is_digest: bool | None
    platform: str
    published_at: str | None
    first_seen_at: str | None
    completeness_status: str
    attachment_kinds: list[str]
    version: int
    updated_at: str
    source_label: str
    source_url: str
    excerpt: str | None
    summary_excerpt: str | None
    cover_asset_url: str | None
    cover_thumbnail_url: str | None
    cover_kind: str | None
    media_count: int
    evaluation: PreviewEvaluation | None
    opportunity: PreviewOpportunity | None


class PreviewPage(TypedDict):
    items: list[PreviewItem]
    total: int
    limit: int
    offset: int
    processing_total: int
    sort: str


class PreviewCard(TypedDict):
    card_id: str
    title: str
    author: str | None
    is_digest: bool | None
    ad: NotRequired[AdMetadata]
    platform: str
    published_at: str | None
    first_seen_at: str | None
    captured_at: str | None
    updated_at: str
    status: str
    completeness_status: str
    version: int
    content_hash: str | None
    processor_version: str | None
    source_label: str
    source_url: str
    raw_item_id: str | None
    source_run_id: str | None
    attachment_count: int
    engagement_metrics: dict[str, int]


class PreviewBodyBlock(TypedDict):
    block_id: str
    card_id: str
    layer: str
    block_kind: str
    content: str
    body: str
    source_url: str | None
    status: str
    sort_order: int
    provenance: str
    raw_payload: JsonObject


class PreviewAsset(TypedDict):
    asset_id: str
    card_id: str
    asset_kind: str
    source_url: str
    remote_url: str | None
    local_path: str | None
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    status: str
    cleanup_state: str
    asset_url: str
    thumbnail_url: str | None


class PreviewMediaGap(TypedDict):
    reason: str
    status: str


class PreviewTrace(TypedDict):
    raw_connector_id: str | None
    raw_item_id: str | None
    source_run_id: str | None


class PreviewDetail(TypedDict):
    knowledge_cards: list[JsonObject]
    card: PreviewCard
    body_blocks: list[PreviewBodyBlock]
    comment_blocks: list[PreviewBodyBlock]
    image_assets: list[PreviewAsset]
    video_assets: list[PreviewAsset]
    media_gap: PreviewMediaGap | None
    missing_reasons: object
    evaluation: PreviewEvaluation | None
    opportunity: PreviewOpportunity | None
    trace: PreviewTrace
