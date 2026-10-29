"""Rows selected by the compact preview queries."""
from typing import TypedDict


class PreviewBlockRow(TypedDict):
    card_id: str
    position: int
    created_at: str
    block_id: str
    layer: str
    block_kind: str
    body: str
    raw_payload: object
    provenance: str


class PreviewAssetRow(TypedDict):
    asset_id: str
    card_id: str
    asset_kind: str
    mime_type: str | None
    status: str
    cleanup_state: str
    local_path: str | None
    created_at: str


class PreviewSourceRow(TypedDict):
    source_id: str
    display_name: str


class PreviewEvaluationRow(TypedDict):
    card_id: str
    quality: int
    relevance: int | None
    passed_quality: int
    attribution_kind: str | None
    attribution_ref: str | None
    content_type: str
    suggested_action: str
    summary: str
    relation_note: str
    evaluated_at: str
    prompt_version: str
    profile_version: int
    model: str
