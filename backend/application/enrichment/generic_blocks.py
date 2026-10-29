from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.enrichment.card_source import matching_raw_item
from backend.application.enrichment.x_blocks._helpers import _upsert_block

_PLATFORM_SPECIFIC_ENRICHMENT = {
    "x",
    "youtube",
    "reddit",
    "xiaohongshu",
    "douyin",
    "scys",
    "zsxq",
}
def enrich_generic_card_blocks(
    card_id: str, base_dir: Path | str, card: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch generic article/page content for non-platform-specific cards.

    X, YouTube, and Reddit have richer platform-specific block builders. Other
    sources use this generic web-content pass during the async content_enrich
    task instead of doing it synchronously while cards are built.
    """
    from backend.infrastructure.storage.repositories import (
        CardContentBlockRepository,
        CardRepository,
        RawItemRepository,
    )

    base_path = Path(base_dir)
    if card is None:
        card = CardRepository(base_path).get_card(card_id)
    if not card or card["source_platform"] in _PLATFORM_SPECIFIC_ENRICHMENT:
        return []

    block_repo = CardContentBlockRepository(base_path)
    raw_item = matching_raw_item(RawItemRepository(base_path), card)
    if raw_item is None:
        return []
    if _has_existing_linked_article(block_repo, card_id):
        return []

    from backend.application.enrichment.web_content import enrich_raw_item_content

    enriched = enrich_raw_item_content(raw_item)
    body = (enriched.raw_text or "").strip()
    original_body = (raw_item.raw_text or "").strip()
    enriched_metadata = enriched.raw_metadata
    original_metadata = raw_item.raw_metadata
    new_images = _new_media_urls(enriched_metadata, original_metadata, "images")
    new_videos = _new_media_urls(enriched_metadata, original_metadata, "videos")
    body_changed = bool(body and body != original_body)
    if not body_changed and not new_images and not new_videos:
        return []

    position = _next_block_position(block_repo, card_id)
    source_url = str(
        enriched_metadata.get("enrichment_source_url")
        or card["source_url"]
        or raw_item.source_url
    )
    status = "body_fetched" if body_changed else "media_discovered"
    block_body = body if body_changed else ""
    _upsert_block(
        block_repo,
        card_id=card_id,
        position=position,
        layer="context",
        block_kind="linked_article",
        status=status,
        body=block_body,
        source_url=source_url,
        provenance="Generic article fetch via async enrichment",
        raw_payload={
            "raw_item_id": raw_item.raw_item_id,
            "connector_id": raw_item.connector_id,
            "enrichment_applied": bool(enriched_metadata.get("enrichment_applied")),
            "images": new_images if not body_changed else list(enriched_metadata.get("images") or []),
            "videos": new_videos if not body_changed else list(enriched_metadata.get("videos") or []),
        },
    )
    return [
        {
            "block_id": f"block_{card_id}_linked_article",
            "card_id": card_id,
            "position": position,
            "layer": "context",
            "block_kind": "linked_article",
            "status": status,
            "body": block_body,
            "source_url": source_url,
            "provenance": "Generic article fetch via async enrichment",
        }
    ]


def _has_existing_linked_article(block_repo: Any, card_id: str) -> bool:
    return any(
        block["block_kind"] == "linked_article"
        and block["status"] in {"enriched", "body_fetched", "media_discovered", "done", "preserved"}
        and (
            bool(block["body"].strip())
            or bool((block.get("raw_payload") or {}).get("images"))
            or bool((block.get("raw_payload") or {}).get("videos"))
        )
        for block in block_repo.list_for_card(card_id)
    )


def _new_media_urls(
    enriched_metadata: dict[str, Any], original_metadata: dict[str, Any], key: str
) -> list[str]:
    existing = {str(url) for url in (original_metadata.get(key) or []) if str(url).strip()}
    return [
        str(url)
        for url in (enriched_metadata.get(key) or [])
        if str(url).strip() and str(url) not in existing
    ]


def _next_block_position(block_repo: Any, card_id: str) -> int:
    blocks = block_repo.list_for_card(card_id)
    return max((block["position"] for block in blocks), default=-1) + 1
