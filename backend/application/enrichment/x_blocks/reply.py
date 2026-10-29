from __future__ import annotations

from typing import Any

from backend.application.enrichment.media.candidates import trusted_x_relation_media_urls
from backend.application.enrichment.media.x_urls import parse_canonical_x_status_url
from backend.application.enrichment.web_content import _run_opencli_json_command
from backend.application.enrichment.x_blocks._helpers import _upsert_block
from backend.domain.models.raw_item import RawItem


def _enrich_reply_parent(
    card_id: str,
    source_url: str,
    repo: Any,
    raw_item: RawItem,
) -> list[dict[str, Any]]:
    """Preserve exactly one replied-to post from the acquisition payload."""
    record = _record(raw_item)
    parent = next(
        (
            record.get(key)
            for key in ("replied_to_tweet", "parent_tweet", "in_reply_to_tweet")
            if isinstance(record.get(key), dict)
        ),
        None,
    )
    parent = parent or {}
    parent_id = (
        record.get("in_reply_to_status_id_str")
        or record.get("in_reply_to_status_id")
        or record.get("in_reply_to_tweet_id")
        or parent.get("id")
    )
    body = str(parent.get("text") or parent.get("full_text") or "").strip()
    parent_url = str(parent.get("url") or parent.get("permalink") or "").strip()
    if not parent_url and parent_id:
        author = parent.get("author") or record.get("in_reply_to_screen_name") or "i"
        if isinstance(author, dict):
            author = author.get("username") or author.get("handle") or "i"
        parent_url = f"https://x.com/{str(author).lstrip('@')}/status/{parent_id}"
    if not body and parent_url and "/status/" in parent_url:
        fetched_parent = _fetch_parent_post(parent_url, parent_id)
        if fetched_parent:
            parent = fetched_parent
            body = str(fetched_parent.get("text") or fetched_parent.get("full_text") or "").strip()
            parent_url = str(
                fetched_parent.get("url") or fetched_parent.get("permalink") or parent_url
            ).strip()

    status = "enriched" if body else "not_found"
    relation_status = parse_canonical_x_status_url(parent_url)
    payload = {
        "content_role": "replied_to",
        "relation_depth": 1,
        "relation_source_url": relation_status.url if relation_status else parent_url,
        "parent_tweet_id": str(parent_id) if parent_id else None,
        "asset_paths": [],
        "media_urls": trusted_x_relation_media_urls(parent),
    }
    block = {
        "block_id": f"block_{card_id}_reply_parent",
        "card_id": card_id,
        "position": 3,
        "layer": "context",
        "block_kind": "reply_parent",
        "status": status,
        "body": body,
        "source_url": parent_url or source_url,
        "provenance": "Replied-to post (one level)",
        "raw_payload": payload,
    }
    _upsert_block(
        repo,
        card_id=card_id,
        position=3,
        layer="context",
        block_kind="reply_parent",
        status=status,
        body=body,
        source_url=parent_url or source_url,
        provenance="Replied-to post (one level)",
        raw_payload=payload,
    )
    return [block]


def _record(raw_item: RawItem) -> dict[str, Any]:
    metadata = raw_item.raw_metadata
    record = metadata.get("opencli_record")
    if isinstance(record, dict):
        return record
    nested = metadata.get("raw_payload")
    if isinstance(nested, dict) and isinstance(nested.get("opencli_record"), dict):
        return nested["opencli_record"]
    return {}


def _fetch_parent_post(parent_url: str, parent_id: Any) -> dict[str, Any] | None:
    payload = _run_opencli_json_command(
        [
            "opencli",
            "twitter",
            "thread",
            parent_url,
            "--limit",
            "1",
            "--format",
            "json",
            "--window",
            "background",
        ],
        timeout=30,
    )
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, list):
        return None
    expected_id = str(parent_id) if parent_id else None
    for item in payload:
        if not isinstance(item, dict):
            continue
        if expected_id is None or str(item.get("id") or "") == expected_id:
            return item
    return None
