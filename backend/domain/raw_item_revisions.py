"""Stable content identity and loss comparison for acquired raw items."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from backend.domain.engagement_metrics import _non_negative_int
from backend.domain.models.raw_item import RawItem


_VOLATILE_KEYS = frozenset({
    "metrics", "public_metrics", "engagement", "engagement_metrics",
    "views", "likes", "replies", "retweets", "bookmarks", "quotes",
    "plays", "comments", "shares", "collects", "upvotes", "score", "points", "num_comments",
    "view_count", "like_count", "reply_count", "repost_count",
    "save_count", "quote_count", "comment_count", "share_count",
    "upvote_count", "favorite_count", "comments_count", "coin_count",
    "readers", "captured_at", "fetched_at", "collected_at", "updated_at",
    "run_id", "last_seen_at", "first_seen_at", "snapshot_ref",
    "source_id", "capture_id", "client_capture_id", "observation_version",
    "canonical_identity_hash", "content_identity_hash", "capture_channel",
    "capture_adapter", "capture_mode", "client", "opencli_command",
    "feed_content_origin", "discovery_reason", "source_lane",
})
_MEDIA_LIST_KEYS = frozenset({"images", "image_urls", "media_urls", "thumbnails", "formats", "videos", "audios"})


def is_counter_value(key: str, value: Any) -> bool:
    if key.casefold() not in _VOLATILE_KEYS:
        return False
    if key.casefold() in {"comments", "score"}:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if isinstance(value, str):
            return _non_negative_int(value.strip().removeprefix("-")) is not None
        return False
    return True


def _stable(value: Any, *, key: str = "") -> Any:
    if isinstance(value, dict):
        return {name: _stable(nested, key=name) for name, nested in sorted(value.items())
                if not is_counter_value(name, nested)}
    if isinstance(value, list):
        items = [_stable(item) for item in value]
        if key.casefold() in _MEDIA_LIST_KEYS:
            items.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return items
    return value


def content_snapshot(item: RawItem) -> dict[str, Any]:
    """Exclude observation state and counters, retaining source content and media."""
    metadata = item.raw_metadata
    if item.source_platform == "zsxq":
        from backend.domain.sources.zsxq_digest import without_digest
        metadata = without_digest(metadata)
    return {
        "source_platform": item.source_platform,
        "source_type": item.source_type,
        "source_url": item.source_url,
        "author": _stable(item.raw_author.model_dump(mode="json") if item.raw_author else {}),
        "published_at": item.raw_timestamps.published_at if item.raw_timestamps else None,
        "title": item.raw_metadata.get("title") or "",
        "raw_text": item.raw_text or "",
        "metadata": _stable(metadata),
    }


def raw_content_hash(item: RawItem) -> str:
    encoded = json.dumps(content_snapshot(item), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def same_barring_replacement(left: Any, right: Any) -> bool:
    """Treat U+FFFD as one lost character only when everything else matches."""
    if isinstance(left, str) and isinstance(right, str):
        return len(left) == len(right) and all(
            a == b or a == "\ufffd" or b == "\ufffd" for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            same_barring_replacement(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            same_barring_replacement(a, b) for a, b in zip(left, right)
        )
    return left == right
