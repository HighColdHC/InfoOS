from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.enrichment.x_blocks._helpers import _upsert_block
from backend.domain.models.raw_item import RawItem


def enrich_reddit_card_blocks(card_id: str, raw_item: RawItem, base_dir: Path) -> list[dict[str, Any]]:
    if raw_item.source_platform != "reddit":
        return []

    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    repo = CardContentBlockRepository(base_dir)
    metadata = raw_item.raw_metadata
    record = metadata.get("opencli_record") if isinstance(metadata.get("opencli_record"), dict) else {}
    selftext = str(metadata.get("selftext") or record.get("selftext") or "").strip()
    title = str(metadata.get("title") or record.get("title") or raw_item.raw_text or "").strip()
    body = _reddit_original_body(title=title, selftext=selftext, fallback=raw_item.raw_text)
    source_url = raw_item.source_url
    blocks: list[dict[str, Any]] = []

    if body:
        _upsert_block(
            repo,
            card_id=card_id,
            position=0,
            layer="original",
            block_kind="reddit_post",
            status="preserved",
            body=body,
            source_url=source_url,
            provenance="Reddit post via OpenCLI",
            raw_payload={"raw_item_id": raw_item.raw_item_id},
        )
        blocks.append(_block(card_id, 0, "original", "reddit_post", "preserved", body, source_url))

    metrics = _reddit_metrics_body(metadata=metadata, record=record, raw_item=raw_item)
    if metrics:
        _upsert_block(
            repo,
            card_id=card_id,
            position=1,
            layer="context",
            block_kind="reddit_metrics",
            status="enriched",
            body=metrics,
            source_url=source_url,
            provenance="Reddit metadata via OpenCLI",
            raw_payload={"metadata": metadata},
        )
        blocks.append(_block(card_id, 1, "context", "reddit_metrics", "enriched", metrics, source_url))

    outbound_url = str(metadata.get("url_overridden_by_dest") or record.get("url_overridden_by_dest") or "").strip()
    if outbound_url and outbound_url != source_url:
        _upsert_block(
            repo,
            card_id=card_id,
            position=2,
            layer="context",
            block_kind="reddit_outbound_link",
            status="enriched",
            body=outbound_url,
            source_url=outbound_url,
            provenance="Reddit outbound link via OpenCLI",
            raw_payload={"post_url": source_url},
        )
        blocks.append(_block(card_id, 2, "context", "reddit_outbound_link", "enriched", outbound_url, outbound_url))

    return blocks


def _reddit_original_body(*, title: str, selftext: str, fallback: str | None) -> str:
    parts = []
    if title:
        parts.append(title)
    if selftext and selftext != title:
        parts.append(selftext)
    if not parts and fallback:
        parts.append(fallback.strip())
    return "\n\n".join(part for part in parts if part)


def _reddit_metrics_body(*, metadata: dict[str, Any], record: dict[str, Any], raw_item: RawItem) -> str:
    subreddit = metadata.get("subreddit") or record.get("subreddit")
    author = (
        (raw_item.raw_author.handle if raw_item.raw_author else None)
        or metadata.get("author")
        or record.get("author")
    )
    score = metadata.get("score", record.get("score"))
    comments = metadata.get("comments", record.get("comments"))
    post_hint = metadata.get("post_hint") or record.get("post_hint")
    parts = []
    if subreddit:
        parts.append(f"r/{str(subreddit).removeprefix('r/')}")
    if author:
        parts.append(f"u/{str(author).removeprefix('u/')}")
    if score is not None:
        parts.append(f"score: {score}")
    if comments is not None:
        parts.append(f"comments: {comments}")
    if post_hint:
        parts.append(f"type: {post_hint}")
    return " · ".join(parts)


def _block(
    card_id: str,
    position: int,
    layer: str,
    block_kind: str,
    status: str,
    body: str,
    source_url: str,
) -> dict[str, Any]:
    return {
        "block_id": f"block_{card_id}_{block_kind}",
        "card_id": card_id,
        "position": position,
        "layer": layer,
        "block_kind": block_kind,
        "status": status,
        "body": body,
        "source_url": source_url,
    }
