"""Compact, read-only list projection for a page of information cards."""

from __future__ import annotations

from collections import defaultdict
import json
import re
from typing import cast

from urllib.parse import quote

from backend.domain.card_titles import display_title
from backend.domain.sources.zsxq_digest import card_is_digest
from backend.domain.timestamps import normalize_timestamp
from backend.read_models.cards import platform_display_label
from backend.read_models.card_preview import _asset_media_kind, _recorded_deliverable_assets, _is_generic_platform_page_block, thumbnail_url

from backend.infrastructure.storage.cards_types import CardRow
from backend.infrastructure.storage.card_preview_query_types import (
    PreviewAssetRow, PreviewBlockRow, PreviewEvaluationRow, PreviewSourceRow,
)
from backend.infrastructure.storage.card_evaluations import CardEvaluationRow
from backend.infrastructure.storage.card_browse_query_types import PreviewOpportunity
from backend.read_models.card_preview_types import PreviewEvaluation, PreviewItem


_EXCLUDED_KINDS = {"enrichment_gap", "llm_context", "media_download_gap", "image", "video", "platform_comments", "comment", "transcript", "video_transcript", "translation"}


def _compact(text: str, limit: int) -> str | None:
    text = re.sub(r"(?m)^\s*(?:https?://\S+\s*)$", "", text)
    text = re.sub(r"(?m)^\s*```[^\n]*$", "", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|>\s*|[-*]\s+(?:\[[ xX]\]\s*)?)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or None


def _excerpt(value: str, title: str, limit: int) -> str | None:
    text = _compact(value, 100_000)
    heading = _compact(title, 100_000)
    if text and heading and text.startswith(heading):
        text = text[len(heading):].lstrip(" ：:—-·。,.，")
    return text[:limit] if text else None


def project_preview_evaluation(row: PreviewEvaluationRow | CardEvaluationRow | None) -> PreviewEvaluation | None:
    """Shared list/detail contract; legacy storage names identify rubric and evaluator."""
    if row is None:
        return None
    evaluated_at = (
        cast(PreviewEvaluationRow, row)["evaluated_at"] if "evaluated_at" in row
        else cast(CardEvaluationRow, row)["created_at"]
    )
    return {
        "quality": row["quality"],
        "relevance": row["relevance"],
        "attribution_kind": row["attribution_kind"],
        "attribution_ref": row["attribution_ref"],
        "content_type": row["content_type"],
        "suggested_action": row["suggested_action"],
        "summary": row["summary"],
        "relation_note": row["relation_note"],
        "prompt_version": row["prompt_version"],
        "profile_version": row["profile_version"],
        "model": row["model"],
        "passed_quality": bool(row["passed_quality"]),
        "evaluated_at": evaluated_at,
    }


def project_preview_items(
    cards: list[CardRow], blocks: list[PreviewBlockRow], assets: list[PreviewAssetRow],
    sources: list[PreviewSourceRow],
    evaluations: list[PreviewEvaluationRow],
    opportunities: dict[str, PreviewOpportunity],
) -> list[PreviewItem]:
    blocks_by_card: dict[str, list[PreviewBlockRow]] = defaultdict(list)
    assets_by_card: dict[str, list[PreviewAssetRow]] = defaultdict(list)
    for block in blocks:
        blocks_by_card[block["card_id"]].append(block)
    for asset in assets:
        assets_by_card[asset["card_id"]].append(asset)
    source_names: dict[str | None, str] = {source["source_id"]: source["display_name"] for source in sources}
    evaluations_by_card = {row["card_id"]: project_preview_evaluation(row) for row in evaluations}
    result: list[PreviewItem] = []
    for card in cards:
        card_id = card["card_id"]
        originals: list[str] = []
        summaries: list[str] = []
        seen: set[str] = set()
        for block in blocks_by_card[card_id]:
            kind = block["block_kind"]
            layer = block["layer"]
            body = block["body"].strip()
            if not body:
                continue
            if layer == "original" and kind not in _EXCLUDED_KINDS and not _is_generic_platform_page_block(card, block):
                if body not in seen:
                    originals.append(body)
                    seen.add(body)
            elif layer == "intelligence" and kind == "summary":
                try:
                    payload = json.loads(cast(str, block["raw_payload"]))
                except (TypeError, ValueError):
                    payload = {}
                if isinstance(payload, dict) and payload.get("purpose") in (None, "", "ai_interpretation"):
                    summaries.append(body)
        deliverable = _recorded_deliverable_assets(assets_by_card[card_id])
        images = [asset for asset in deliverable if _asset_media_kind(asset) == "image"]
        videos = [asset for asset in deliverable if _asset_media_kind(asset) == "video"]
        media = images + videos
        source_name = source_names.get(card["source_id"]) or card["source_author"] or card["source_id"] or platform_display_label(card["source_platform"])
        result.append({
            "card_id": card_id,
            "title": display_title(card["source_platform"], card["title"], card["content"]),
            "author": card["source_author"],
            "is_digest": card_is_digest(card),
            "platform": card["source_platform"],
            "published_at": normalize_timestamp(card["published_at"]),
            "first_seen_at": card["first_seen_at"],
            "completeness_status": "processing" if card["status"] == "processing" else card["completeness_status"],
            "attachment_kinds": sorted({asset["asset_kind"] for asset in deliverable}),
            "version": card["version"],
            "updated_at": card["updated_at"],
            "source_label": source_name,
            "source_url": card["source_url"],
            "excerpt": _excerpt("\n".join(originals) if originals else card["content"], card["title"], 280),
            "summary_excerpt": _compact("\n".join(summaries), 280),
            "cover_asset_url": "/api/card-preview/assets/" + quote(media[0]["asset_id"], safe="") if media else None,
            "cover_thumbnail_url": thumbnail_url(media[0]["asset_id"], media[0]["content_hash"],
                                                  media[0]["thumbnail_status"], media[0]["thumbnail_hash"]) if media else None,
            "cover_kind": _asset_media_kind(media[0]) if media else None,
            "media_count": len(media),
            "evaluation": evaluations_by_card.get(card_id),
            "opportunity": opportunities.get(card_id),
        })
    return result
