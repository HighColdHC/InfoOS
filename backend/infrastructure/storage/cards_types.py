"""Typed card projections returned by the card repository."""
from typing import TypedDict

from backend.domain.json_types import JsonObject


class CardRow(TypedDict):
    card_id: str
    card_type: str
    version: int
    content_hash: str | None
    updated_at: str
    status: str
    completeness_status: str
    missing_reasons: object
    processor_version: str | None
    raw_connector_id: str | None
    raw_item_id: str | None
    source_run_id: str | None
    source_id: str | None
    source_platform: str
    source_type: str
    source_url: str
    source_author: str | None
    title: str
    content: str
    summary: str
    primary_domain_id: str | None
    secondary_domain_ids: object
    published_at: str | None
    captured_at: str
    processed_at: str
    workflow_status: str
    ignored: bool
    skimmed: bool
    raw_payload: JsonObject
    first_seen_at: str | None
    content_quality: object
    content_needs_enrichment: bool
