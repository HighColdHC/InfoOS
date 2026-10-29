"""Pure ZSXQ observation metadata and stable post identity."""
from __future__ import annotations

from typing import Any
from collections.abc import Mapping
from urllib.parse import urlsplit


def zsxq_topic_url(source_url: str, topic_id: str) -> str:
    parsed = urlsplit(source_url)
    if parsed.hostname in {"wx.zsxq.com", "zsxq.com"} and parsed.path.startswith("/topic/"):
        topic_id = parsed.path.split("/topic/", 1)[1].split("/", 1)[0]
    return f"https://wx.zsxq.com/topic/{topic_id}" if topic_id else source_url


def card_is_digest(card: Mapping[str, Any]) -> bool | None:
    if card.get("source_platform", card.get("platform")) != "zsxq":
        return None
    payload = card.get("raw_payload") or {}
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("raw_payload", payload)
    value = metadata.get("is_digest") if isinstance(metadata, dict) else None
    return value if isinstance(value, bool) else None


def without_digest(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: without_digest(item) for key, item in value.items() if key not in {"is_digest", "_zsxq_digest_observation", "_zsxq_digest_only_existing"}}
    if isinstance(value, list):
        return [without_digest(item) for item in value]
    return value
