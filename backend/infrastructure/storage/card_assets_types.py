"""Decoded card asset row."""
from typing import TypedDict

from backend.domain.json_types import JsonObject


class CardAssetRow(TypedDict):
    asset_id: str
    card_id: str
    asset_kind: str
    source_url: str
    remote_url: str | None
    local_path: str | None
    mime_type: str | None
    size_bytes: int | None
    content_hash: str | None
    preservation_class: str
    status: str
    cleanup_state: str
    raw_payload: JsonObject
    created_at: str
    updated_at: str
