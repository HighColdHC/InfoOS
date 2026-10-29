"""Decoded card content block row."""
from typing import TypedDict

from backend.domain.json_types import JsonObject


class CardBodyBlockRow(TypedDict):
    block_id: str
    card_id: str
    position: int
    layer: str
    block_kind: str
    status: str
    body: str
    source_url: str | None
    provenance: str
    raw_payload: JsonObject


class CardContentBlockRow(CardBodyBlockRow):
    segments: object
    created_at: str
    updated_at: str
