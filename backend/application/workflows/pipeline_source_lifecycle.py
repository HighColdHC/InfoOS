"""Resolve durable source ownership for pipeline reconciliation."""

from __future__ import annotations

from typing import Any


def raw_item_source_id(raw_item: dict[str, Any]) -> str:
    source_id = str(raw_item.get("source_id") or "")
    if source_id:
        return source_id
    payload = raw_item.get("raw_payload")
    if not isinstance(payload, dict):
        return ""
    metadata = payload.get("raw_metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("source_id") or "")


def card_source_id(card: dict[str, Any]) -> str:
    source_id = str(card.get("source_id") or "")
    if source_id:
        return source_id
    payload = card.get("raw_payload")
    if not isinstance(payload, dict):
        return ""
    source_id = str(payload.get("source_id") or "")
    if source_id:
        return source_id
    nested = payload.get("raw_payload")
    if not isinstance(nested, dict):
        return ""
    return str(nested.get("source_id") or "")
