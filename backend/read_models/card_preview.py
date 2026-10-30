"""Read-only projection for finalized information-card preview pages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TypeVar, TypedDict, cast
from collections.abc import Sequence
from urllib.parse import quote

from backend.domain.advertising import advertising_projection
from backend.domain.engagement_metrics import extract_engagement_metrics
from backend.domain.card_titles import display_title
from backend.domain.sources.zsxq_digest import card_is_digest
from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    RawItemRepository,
)
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.read_models.cards import source_display_metadata
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.read_models._connections import shared_read_connection
from backend.read_models.knowledge import knowledge_for_card

from backend.infrastructure.storage.cards_types import CardRow
from backend.infrastructure.storage.card_assets_types import CardAssetRow
from backend.infrastructure.storage.card_asset_thumbnails import CardAssetThumbnailRepository
from backend.infrastructure.storage.card_asset_thumbnails_types import CardAssetThumbnailRow
from backend.infrastructure.storage.card_content_blocks_types import CardBodyBlockRow
from backend.read_models.card_preview_types import (
    PreviewPage, PreviewDetail, PreviewBodyBlock, PreviewAsset, PreviewMediaGap,
)


class AssetMediaRow(TypedDict):
    asset_kind: str
    mime_type: str | None


class DeliverableAssetRow(AssetMediaRow):
    cleanup_state: str
    status: str
    local_path: str | None


class GenericPageCard(TypedDict):
    source_platform: str


class GenericPageBlock(TypedDict):
    block_kind: str
    provenance: str


_Asset = TypeVar("_Asset", bound=DeliverableAssetRow)


_MACHINE_BLOCK_KINDS = {"enrichment_gap", "llm_context", "media_download_gap"}
_MEDIA_BLOCK_KINDS = {"image", "video"}
_COMMENT_BLOCK_KINDS = {"platform_comments", "comment"}
_PLATFORM_PAGE_ENRICHMENT_BLOCKED = {"xiaohongshu", "douyin", "scys", "zsxq"}
_GENERIC_ARTICLE_PROVENANCE = "Generic article fetch via async enrichment"


@shared_read_connection
def read_card_preview_page(
    base_dir: Path | str,
    *,
    query: str | None = None,
    platform: str | None = None,
    source_id: str | None = None,
    evaluation: str | None = None,
    completeness: str | None = None,
    status: str = "active",
    sort: str = "published_desc",
    recommended: bool = False,
    digest: bool | None = None,
    limit: int = 25,
    offset: int = 0,
    published_since: datetime | None = None,
    published_until: datetime | None = None,
) -> PreviewPage:
    """Return finalized cards by default, or durable in-progress card rows."""

    base_path = Path(base_dir)
    from backend.infrastructure.storage.card_preview_query import (
        read_preview_rows, read_preview_page_content, read_processing_total,
    )
    from backend.read_models.card_preview_list import project_preview_items
    bounded_limit = max(1, min(int(limit), 100))
    bounded_offset = max(0, int(offset))
    page_cards, total = read_preview_rows(
        base_path, query=query, platform=_normalized_filter(platform),
        completeness=_normalized_filter(completeness), status=status, sort=sort,
        limit=bounded_limit, offset=bounded_offset,
        published_since=published_since, published_until=published_until,
        recommended=recommended, digest=digest,
        source_id=_normalized_filter(source_id), evaluation=evaluation,
    )
    blocks, assets, sources, evaluations = read_preview_page_content(
        base_path, [card["card_id"] for card in page_cards],
        list({card["source_id"] for card in page_cards if card["source_id"]}),
    )
    from backend.infrastructure.storage.card_browse_query import read_preview_opportunities
    opportunities = read_preview_opportunities(base_path, [card["card_id"] for card in page_cards])
    return {
        "items": project_preview_items(page_cards, blocks, assets, sources, evaluations, opportunities),
        "total": total, "limit": bounded_limit, "offset": bounded_offset,
        "processing_total": read_processing_total(base_path),
        "sort": sort,
    }



@shared_read_connection
def read_card_preview_detail(base_dir: Path | str, card_id: str) -> PreviewDetail | None:
    """Return the user-readable blocks, media facts, and collection trace for one card."""

    base_path = Path(base_dir)
    card = CardRepository(base_path).get_card(card_id)
    if card is None or not _is_stable_card(card):
        return None

    assets = _deliverable_assets(base_path, CardAssetRepository(base_path).list_for_card(card_id))
    states = CardAssetThumbnailRepository(base_path).states_for([a["asset_id"] for a in assets])
    image_assets = [asset for asset in assets if _asset_media_kind(asset) == "image"]
    video_assets = [asset for asset in assets if _asset_media_kind(asset) == "video"]
    body_blocks = _readable_body_blocks(base_path, card)
    comment_blocks = _readable_comment_blocks(base_path, card_id)
    has_original_body = any(block["layer"] == "original" for block in body_blocks)
    if not has_original_body and card["content"].strip():
        body_blocks = [
            {
                "block_id": f"fallback-{card_id}-content",
                "card_id": card_id,
                "position": 0,
                "layer": "original",
                "block_kind": "card_content",
                "status": "preserved",
                "body": card["content"],
                "source_url": card["source_url"],
                "provenance": "card content fallback",
                "raw_payload": {},
            },
            *body_blocks,
        ]
    source = source_display_metadata(base_path, card)
    from backend.read_models.card_preview_list import project_preview_evaluation

    first_seen_at = normalize_timestamp(card["first_seen_at"])
    raw_item = None
    if card["raw_connector_id"] and card["raw_item_id"]:
        raw_item = RawItemRepository(base_path).get_for_connector(card["raw_connector_id"], card["raw_item_id"])
    metrics_payload = card["raw_payload"]
    if raw_item is not None:
        latest_payload = raw_item["raw_payload"]
        metrics_payload = latest_payload.get("raw_metadata", latest_payload)
    from backend.infrastructure.storage.card_browse_query import read_preview_opportunities
    opportunity = read_preview_opportunities(base_path, [card_id]).get(card_id)
    return {
        "opportunity": opportunity,
        "knowledge_cards": knowledge_for_card(base_dir, card_id),
        "card": {
            "card_id": card["card_id"],
            "title": display_title(card["source_platform"], card["title"], card["content"]),
            "author": card["source_author"],
            "is_digest": card_is_digest(card),
            **advertising_projection(card["raw_payload"]),
            "platform": card["source_platform"],
            "published_at": normalize_timestamp(card["published_at"]),
            "first_seen_at": first_seen_at,
            "captured_at": first_seen_at,
            "updated_at": card["updated_at"],
            "status": card["status"],
            "completeness_status": card["completeness_status"],
            "version": card["version"],
            "content_hash": card["content_hash"],
            "processor_version": card["processor_version"],
            "source_label": source["source_name"],
            "source_url": card["source_url"],
            "raw_item_id": card["raw_item_id"],
            "source_run_id": card["source_run_id"],
            "attachment_count": len(image_assets) + len(video_assets),
            "engagement_metrics": extract_engagement_metrics(card["source_platform"], metrics_payload),
        },
        "body_blocks": [_project_body_block(block) for block in body_blocks],
        "comment_blocks": [_project_body_block(block) for block in comment_blocks],
        "image_assets": [_project_asset(asset, states.get(asset["asset_id"])) for asset in image_assets],
        "video_assets": [_project_asset(asset, states.get(asset["asset_id"])) for asset in video_assets],
        "media_gap": _project_media_gap(base_path, card_id),
        "missing_reasons": card["missing_reasons"],
        "evaluation": project_preview_evaluation(
            CardEvaluationRepository(base_path).latest_for_card(card_id)
        ),
        "trace": {
            "raw_connector_id": card["raw_connector_id"],
            "raw_item_id": card["raw_item_id"],
            "source_run_id": card["source_run_id"],
        },
    }


def _project_media_gap(base_dir: Path, card_id: str) -> PreviewMediaGap | None:
    gaps = [
        block for block in CardContentBlockRepository(base_dir).list_for_card(card_id)
        if block["block_kind"] == "media_download_gap" and block["status"] != "resolved"
    ]
    if not gaps:
        return None
    latest = max(gaps, key=lambda block: (
        normalize_timestamp(block["updated_at"]) or "", block["block_id"],
    ))
    return {
        "reason": str((latest["raw_payload"] or {}).get("reason") or ""),
        "status": latest["status"],
    }


def _normalized_filter(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _is_stable_card(card: CardRow) -> bool:
    return card["status"] == "active" and card["version"] >= 1 and bool((card["content_hash"] or "").strip())


def _readable_body_blocks(base_dir: Path, card: CardRow) -> list[CardBodyBlockRow]:
    return [
        block
        for block in CardContentBlockRepository(base_dir).list_for_card(card["card_id"])
        if block["block_kind"] not in _MACHINE_BLOCK_KINDS | _MEDIA_BLOCK_KINDS | _COMMENT_BLOCK_KINDS
        and not _is_generic_platform_page_block(card, block)
        and block["body"].strip()
    ]


def _is_generic_platform_page_block(card: GenericPageCard, block: GenericPageBlock) -> bool:
    return (
        card["source_platform"] in _PLATFORM_PAGE_ENRICHMENT_BLOCKED
        and block["block_kind"] == "linked_article"
        and block["provenance"] == _GENERIC_ARTICLE_PROVENANCE
    )


def _readable_comment_blocks(base_dir: Path, card_id: str) -> list[CardBodyBlockRow]:
    return [
        block
        for block in CardContentBlockRepository(base_dir).list_for_card(card_id)
        if block["block_kind"] in _COMMENT_BLOCK_KINDS and block["body"].strip()
    ]


def _project_body_block(block: CardBodyBlockRow) -> PreviewBodyBlock:
    body = block["body"]
    return {
        "block_id": block["block_id"],
        "card_id": block["card_id"],
        "layer": block["layer"],
        "block_kind": block["block_kind"],
        "content": body,
        "body": body,
        "source_url": block["source_url"],
        "status": block["status"],
        "sort_order": block["position"],
        "provenance": block["provenance"],
        "raw_payload": block["raw_payload"],
    }


def _asset_media_kind(asset: AssetMediaRow) -> str | None:
    kind = asset["asset_kind"].casefold()
    mime_type = (asset["mime_type"] or "").casefold()
    if kind == "image" or mime_type.startswith("image/"):
        return "image"
    if kind == "video" or mime_type.startswith("video/"):
        return "video"
    return None


_DELIVERABLE_STATUSES = frozenset({"done", "downloaded", "enriched", "preserved", "ready", "understood"})


def _recorded_deliverable_assets(assets: Sequence[_Asset]) -> list[_Asset]:
    return [a for a in assets if a["cleanup_state"] == "present" and a["status"] in _DELIVERABLE_STATUSES and a["local_path"]]


def thumbnail_url(asset_id: str, content_hash: str | None,
                  thumbnail_status: str | None, thumbnail_hash: str | None) -> str | None:
    if thumbnail_status != "ready" or thumbnail_hash != content_hash:
        return None
    return "/api/card-preview/assets/" + quote(asset_id, safe="") + "/thumbnail"


def _deliverable_assets(base_dir: Path, assets: Sequence[_Asset]) -> list[_Asset]:
    assets_root = (resolve_data_dir(base_dir) / "assets").resolve()
    result = []
    for asset in _recorded_deliverable_assets(assets):
        target = (assets_root / cast(str, asset["local_path"])).resolve()
        try:
            target.relative_to(assets_root)
        except ValueError:
            continue
        if target.is_file():
            result.append(asset)
    return result


def _project_asset(asset: CardAssetRow, thumbnail: CardAssetThumbnailRow | None) -> PreviewAsset:
    asset_id = asset["asset_id"]
    return {
        "asset_id": asset_id,
        "card_id": asset["card_id"],
        "asset_kind": asset["asset_kind"],
        "source_url": asset["source_url"],
        "remote_url": asset["remote_url"],
        "local_path": asset["local_path"],
        "mime_type": asset["mime_type"],
        "size_bytes": asset["size_bytes"],
        "content_hash": asset["content_hash"],
        "status": asset["status"],
        "cleanup_state": asset["cleanup_state"],
        "asset_url": "/api/card-preview/assets/" + quote(asset_id, safe=""),
        "thumbnail_url": thumbnail_url(asset_id, asset["content_hash"],
                                       thumbnail["status"] if thumbnail else None,
                                       thumbnail["source_content_hash"] if thumbnail else None),
    }
