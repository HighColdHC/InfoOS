"""Small persisted card metadata; source bodies stay in their owning rows."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from backend.domain.advertising import AD_FIELDS
from backend.domain.models.raw_item import RawItem
from backend.domain.raw_item_revisions import content_snapshot, is_counter_value

CARD_METADATA_FIELDS = frozenset({
    "is_digest", "is_ad", *AD_FIELDS, "content_quality", "content_needs_enrichment",
    "transcript_fallback", "feed_content_origin", "feed_content_mode", "feed_provider",
    "content_source", "content_source_status", "content_source_completeness",
    "content_source_url", "reason", "reached_end", "blocks",
    "article_url", "article_title",
})
CARD_COUNTER_FIELDS = frozenset({
    "views", "likes", "replies", "retweets", "bookmarks", "quotes", "plays",
    "comments", "shares", "collects", "like_count", "favorite_count", "comments_count",
    "coin_count", "readers", "upvotes", "score",
})
CARD_METADATA_CONTAINERS = ("raw_payload", "raw_metadata", "metadata", "opencli_record",
                            "metrics", "public_metrics", "external_doc_read")


def compact_card_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Preserve reader paths and precedence, excluding bodies and source records."""
    result = {}
    for key, value in metadata.items():
        if key == "zsxq_article_read" and isinstance(value, dict):
            result[key] = {name: value[name] for name in (
                "article_url", "article_title", "status", "policy_version", "read_attempts", "reason",
            ) if name in value}
        elif key in CARD_METADATA_FIELDS or (
            key in CARD_COUNTER_FIELDS and is_counter_value(key, value)
            and not isinstance(value, (dict, list))
        ):
            result[key] = value
        elif key in CARD_METADATA_CONTAINERS and isinstance(value, dict):
            nested = compact_card_metadata(value)
            if nested:
                result[key] = nested
    return result


def card_metadata_hash(item: RawItem, metadata: dict[str, Any]) -> str:
    """Keep enrichment inputs sensitive to source changes without copying them."""
    stable = content_snapshot(item.model_copy(update={"raw_metadata": metadata}))["metadata"]
    if item.source_platform == "zsxq":
        for key in ("source_scope", "research_platform", "research_batch_id", "matched_keywords",
                    "opencli_record", "topic_id", "type", "full_text"):
            stable.pop(key, None)
    return sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
