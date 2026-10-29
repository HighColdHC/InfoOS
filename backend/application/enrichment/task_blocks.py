from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from backend.application.enrichment.card_source import matching_raw_item
from backend.domain.failures import OpenCLISiteLeaseTimeout
from backend.domain.models.raw_item import RawAuthor, RawItem
from backend.infrastructure.observability.logging import get_logger

_logger = get_logger("enrichment.task_blocks")


def enrich_card_blocks_for_task(
    card_id: str, base_dir: Path | str, card: dict[str, Any] | None = None, *,
    external_doc_force_browser: bool = False,
) -> int:
    """Run async content-block enrichment for one card.

    This is the canonical content_enrich task implementation. It writes
    platform/generic context blocks only; media download, OCR, transcription,
    context packing, and summary are separate downstream tasks.
    """
    from backend.infrastructure.storage.repositories import CardRepository

    if card is None:
        card = CardRepository(base_dir).get_card(card_id)
    if not card:
        return 0
    platform = card["source_platform"]
    if platform == "x":
        return _enrich_x_card_blocks(card_id, base_dir, card)
    if platform == "reddit":
        return _enrich_reddit_card_blocks(card_id, base_dir, card)
    if platform == "youtube":
        return _enrich_youtube_card_blocks(card_id, base_dir, card)
    if platform in {"xiaohongshu", "douyin", "scys", "zsxq"}:
        if platform == "zsxq":
            from backend.application.enrichment.zsxq_article import enrich_zsxq_article

            enrich_zsxq_article(card, base_dir)
            card = CardRepository(base_dir).get_card(card_id)
        added = _enrich_platform_comment_blocks(card_id, base_dir, card)
        if platform in {"scys", "zsxq"}:
            added += _enrich_external_doc_blocks(
                card_id, base_dir, card, force_browser=external_doc_force_browser)
        return added
    return _enrich_generic_card_blocks(card_id, base_dir, card)


def _enrich_x_card_blocks(card_id: str, base_dir: Path | str, card: dict[str, Any]) -> int:
    from backend.application.enrichment.x_blocks._shared import enrich_x_card_blocks
    from backend.infrastructure.storage.repositories import RawItemRepository

    raw_item = matching_raw_item(RawItemRepository(base_dir), card)
    if raw_item is None:
        return 0
    return len(enrich_x_card_blocks(card_id, raw_item, Path(base_dir)))


def _enrich_reddit_card_blocks(card_id: str, base_dir: Path | str, card: dict[str, Any]) -> int:
    from backend.application.enrichment.reddit_blocks import enrich_reddit_card_blocks
    from backend.infrastructure.storage.repositories import RawItemRepository

    raw_item = matching_raw_item(RawItemRepository(base_dir), card)
    if raw_item is None:
        return 0
    return len(enrich_reddit_card_blocks(card_id, raw_item, Path(base_dir)))


def _enrich_youtube_card_blocks(card_id: str, base_dir: Path | str, card: dict[str, Any]) -> int:
    from backend.application.enrichment.card_source import card_with_source_payload
    from backend.application.enrichment.youtube_blocks import enrich_youtube_card_blocks

    source_card = card_with_source_payload(card, base_dir)
    raw_payload = source_card["raw_payload"] if isinstance(source_card["raw_payload"], dict) else {}
    inner_payload = raw_payload.get("raw_payload") if isinstance(raw_payload.get("raw_payload"), dict) else {}
    opencli_record = inner_payload.get("opencli_record") if isinstance(inner_payload.get("opencli_record"), dict) else {}
    trace = raw_payload.get("trace") if isinstance(raw_payload.get("trace"), dict) else {}
    raw_item = RawItem(
        raw_item_id=str(source_card["raw_item_id"] or trace.get("raw_item_id") or card_id),
        connector_id=source_card["raw_connector_id"] or source_card["source_id"] or "youtube",
        source_platform="youtube",
        source_type=source_card["source_type"],
        source_url=source_card["source_url"],
        captured_at=source_card["captured_at"],
        raw_text=str(
            source_card["content"]
            or raw_payload.get("normalized_content")
            or raw_payload.get("summary")
            or opencli_record.get("text")
            or opencli_record.get("title")
            or ""
        ),
        raw_author=RawAuthor(name=source_card["source_author"] or None),
        raw_metadata={**inner_payload, **raw_payload,
                      "published_at": source_card["published_at"] or raw_payload.get("published_at"),
                      "raw_payload": inner_payload},
    )
    try:
        return len(enrich_youtube_card_blocks(card_id, raw_item, base_dir))
    except (OSError, ValueError) as exc:
        _logger.exception("YouTube subtitle enrichment failed for %s", card_id)
        return _write_youtube_subtitle_failure(card_id, base_dir, exc, card)


def _write_youtube_subtitle_failure(
    card_id: str, base_dir: Path | str, exc: Exception, card: dict[str, Any],
) -> int:
    from backend.application.enrichment.transcripts.subtitles import SUBTITLE_POLICY_VERSION
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    repo = CardContentBlockRepository(base_dir)
    repo.upsert_block(
        {
            "block_id": f"block_{card_id}_youtube_subtitle_attempt",
            "card_id": card_id,
            "position": _next_block_position(repo, card_id),
            "layer": "context",
            "block_kind": "enrichment",
            "status": "blocked",
            "body": "",
            "segments": [],
            "source_url": card["source_url"],
            "provenance": "YouTube subtitle attempt",
            "raw_payload": {
                "subtitle_attempt_status": "failed",
                "subtitle_policy_version": SUBTITLE_POLICY_VERSION,
                "usable_transcript": False,
                "fallback": "none",
                "transcript_fallback": "none",
                "strategy": "yt_dlp_inventory",
                "language": None,
                "manual_or_auto": None,
                "video_id": None,
                "sidecar_reference": None,
                "sidecar_sha256": None,
                "evidence": {"reason": "subtitle_enrichment_exception", "error_type": type(exc).__name__},
            },
        }
    )
    return 1


def _enrich_generic_card_blocks(card_id: str, base_dir: Path | str, card: dict[str, Any]) -> int:
    from backend.application.enrichment.generic_blocks import enrich_generic_card_blocks

    try:
        return len(enrich_generic_card_blocks(card_id, Path(base_dir), card))
    except (OSError, subprocess.SubprocessError):
        _logger.exception("Generic content enrichment failed for %s", card_id)
        return 0


def _enrich_platform_comment_blocks(card_id: str, base_dir: Path | str, card: dict[str, Any]) -> int:
    from backend.application.enrichment.platform_comments import enrich_platform_comments

    try:
        return enrich_platform_comments(card_id, base_dir, card)
    except (OSError, subprocess.SubprocessError) as exc:
        return _write_enrichment_gap(
            card_id,
            base_dir,
            block_id_suffix="platform_comments_exception",
            reason="platform_comments_exception",
            error=str(exc),
            card=card,
        )


def _enrich_external_doc_blocks(
    card_id: str, base_dir: Path | str, card: dict[str, Any], *,
    force_browser: bool = False,
) -> int:
    from backend.application.enrichment.external_docs import enrich_external_docs

    try:
        return enrich_external_docs(card_id, base_dir, card=card, force_browser=force_browser)
    except OpenCLISiteLeaseTimeout:
        raise
    except OSError as exc:
        return _write_enrichment_gap(
            card_id,
            base_dir,
            block_id_suffix="external_doc_exception",
            reason="external_doc_exception",
            error=str(exc),
            card=card,
        )


def _write_enrichment_gap(
    card_id: str,
    base_dir: Path | str,
    *,
    block_id_suffix: str,
    reason: str,
    error: str,
    card: dict[str, Any],
) -> int:
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    _logger.warning("Content enrichment gap for %s: %s (%s)", card_id, reason, error)
    repo = CardContentBlockRepository(base_dir)
    repo.upsert_block(
        {
            "block_id": f"block_{card_id}_{block_id_suffix}",
            "card_id": card_id,
            "position": _next_block_position(repo, card_id),
            "layer": "context",
            "block_kind": "enrichment_gap",
            "status": "blocked",
            "body": f"富化步骤异常，已记录缺口：{reason}",
            "segments": [],
            "source_url": card["source_url"],
            "provenance": "content enrichment exception",
            "raw_payload": {"reason": reason, "error": error},
        }
    )
    return 1


def _next_block_position(repo: Any, card_id: str) -> int:
    return max((block["position"] for block in repo.list_for_card(card_id)), default=-1) + 1
