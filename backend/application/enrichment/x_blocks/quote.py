from __future__ import annotations

from typing import Any

from backend.application.enrichment.media.candidates import trusted_x_relation_media_urls
from backend.application.enrichment.media.x_urls import parse_canonical_x_status_url
from backend.application.enrichment.web_content import _run_opencli_json_command
from backend.application.enrichment.x_blocks._helpers import _upsert_block
from backend.domain.models.raw_item import RawItem


def _enrich_quote(
    card_id: str,
    tweet_id: str,
    source_url: str,
    repo: Any,
    raw_item: RawItem,
) -> list[dict[str, Any]]:
    """Fetch quoted post content (first depth only) and any media.
    Returns list of block dicts."""
    quote_payload = _run_opencli_json_command(
        ["opencli", "twitter", "quote", tweet_id, "--format", "json"],
        timeout=15,
    )
    status: str
    body: str
    provenance: str
    quote_source_url: str | None = None
    quoted_author: dict[str, Any] | None = None
    quoted_at: str | None = None
    media_kind: str | None = None
    metrics: dict[str, Any] | None = None
    relation_media_urls: list[str] = []

    if quote_payload is None:
        status = "not_found"
        body = ""
        provenance = "No quote data available"
    elif isinstance(quote_payload, dict):
        quote_source_url = str(quote_payload.get("url") or quote_payload.get("permalink") or "")
        relation_media_urls = trusted_x_relation_media_urls(quote_payload)
        quoted_text = str(quote_payload.get("text") or "").strip()
        quoted_author = _author_payload(quote_payload.get("author"))
        quoted_at = quote_payload.get("created_at")
        media_kind = _media_kind(quote_payload)
        metrics = _metrics_payload(quote_payload)
        if quoted_text:
            status = "enriched"
            body = quoted_text
            provenance = "Quoted tweet"
        else:
            status = "not_found"
            body = ""
            provenance = "No text in quote payload"
    elif isinstance(quote_payload, str):
        body_val = quote_payload.strip()
        if body_val:
            status = "enriched"
            body = body_val
            provenance = "Quoted tweet (string payload)"
        else:
            status = "not_found"
            body = ""
            provenance = "Quote payload is empty"
    else:
        status = "not_found"
        body = ""
        provenance = "No quote data available"

    # Fallback: try to extract quote info from raw metadata
    if not body:
        raw_metadata = raw_item.raw_metadata
        opencli_record = raw_metadata.get("opencli_record", {})
        if isinstance(opencli_record, dict):
            quote = opencli_record.get("quoted_status") or opencli_record.get("quoted_tweet") or opencli_record.get("quote")
            if isinstance(quote, dict):
                body = str(quote.get("text") or quote.get("full_text") or "")
                if body:
                    status = "enriched"
                    provenance = "Quoted tweet (from metadata)"
                    quote_source_url = str(quote.get("url") or quote.get("permalink") or quote_source_url or "")
                    quoted_author = quoted_author or _metadata_author_payload(quote)
                    quoted_at = quoted_at or quote.get("created_at")
                    media_kind = media_kind or _media_kind(quote)
                    metrics = metrics or _metrics_payload(quote)
                    relation_media_urls = relation_media_urls or trusted_x_relation_media_urls(quote)

    relation_status = parse_canonical_x_status_url(quote_source_url or "")
    raw_payload: dict[str, Any] = {
        "content_role": "quoted",
        "relation_depth": 1,
        "relation_source_url": relation_status.url if relation_status else quote_source_url,
        "quoted_author": quoted_author,
        "quoted_at": quoted_at,
        "asset_paths": [],
        "media_kind": media_kind,
        "media_urls": relation_media_urls,
        "metrics": metrics,
    }

    _upsert_block(
        repo,
        card_id=card_id,
        position=4,
        layer="context",
        block_kind="quote",
        status=status,
        body=body,
        source_url=quote_source_url or source_url,
        provenance=provenance,
        raw_payload=raw_payload,
    )
    return [
        {
            "block_id": f"block_{card_id}_quote",
            "card_id": card_id,
            "position": 4,
            "layer": "context",
            "block_kind": "quote",
            "status": status,
            "body": body,
            "source_url": quote_source_url or source_url,
            "provenance": provenance,
            "raw_payload": raw_payload,
        }
    ]
def _author_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "name": value.get("name"),
            "handle": value.get("username") or value.get("handle"),
            "url": value.get("url"),
        }
    if isinstance(value, str) and value.strip():
        handle = value.strip().lstrip("@")
        return {"name": value.strip(), "handle": handle, "url": f"https://x.com/{handle}"}
    return None


def _metadata_author_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    author = payload.get("author")
    name = payload.get("name")
    if isinstance(author, dict):
        return _author_payload(author)
    if isinstance(author, str) and author.strip():
        handle = author.strip().lstrip("@")
        return {
            "name": str(name or author).strip(),
            "handle": handle,
            "url": str(payload.get("author_url") or f"https://x.com/{handle}"),
        }
    if isinstance(name, str) and name.strip():
        return {"name": name.strip(), "handle": None, "url": None}
    return None


def _media_kind(payload: dict[str, Any]) -> str | None:
    explicit = payload.get("media_kind") or payload.get("media_type")
    if explicit:
        return str(explicit)
    media = payload.get("media")
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict):
                candidate = item.get("type") or item.get("media_type")
                if candidate:
                    return str(candidate)
            elif isinstance(item, str):
                candidate = _media_kind_from_url(item)
                if candidate:
                    return candidate
    media_urls = payload.get("media_urls")
    if isinstance(media_urls, list):
        for item in media_urls:
            if isinstance(item, str):
                candidate = _media_kind_from_url(item)
                if candidate:
                    return candidate
    return None


def _metrics_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get("metrics") or payload.get("public_metrics")
    if isinstance(value, dict):
        return value
    metrics = {
        "reply_count": payload.get("reply_count", payload.get("replies")),
        "retweet_count": payload.get("retweet_count", payload.get("retweets")),
        "like_count": payload.get("like_count", payload.get("likes")),
        "view_count": payload.get("view_count", payload.get("views")),
    }
    compact = {key: val for key, val in metrics.items() if val is not None}
    return compact or None


def _media_kind_from_url(url: str) -> str | None:
    value = url.lower()
    if any(token in value for token in (".mp4", ".mov", ".webm", "video.twimg.com")):
        return "video"
    if any(token in value for token in (".jpg", ".jpeg", ".png", ".gif", ".webp", "pbs.twimg.com")):
        return "image"
    return None
