"""SQL projections used by plugin card delivery."""
from typing import TypedDict


class PluginCandidateRow(TypedDict):
    card_id: str
    card_type: str
    version: int
    content_hash: str | None
    title: str
    source_id: str | None
    source_platform: str
    published_at: str | None
    updated_at: str
    status: str
    completeness_status: str
    source_url: str


class PluginContentRow(TypedDict):
    card_id: str
    content: str
    summary: str
    raw_payload: object


class PluginCatalogRow(PluginCandidateRow):
    content: str
    summary: str
    raw_payload: object


class PluginAssetRow(TypedDict):
    asset_id: str
    card_id: str
    asset_kind: str
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    status: str
    cleanup_state: str
    local_path: str | None


class PluginStableStatRow(TypedDict):
    source_id: str
    card_count: int
    latest: str | None
