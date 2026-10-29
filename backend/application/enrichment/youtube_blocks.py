from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.enrichment.transcripts.subtitles import (
    SUBTITLE_POLICY_VERSION,
    fetch_youtube_subtitle,
)
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.paths import resolve_data_dir


def enrich_youtube_card_blocks(card_id: str, raw_item: RawItem, base_dir: Path | str) -> list[dict[str, Any]]:
    if raw_item.source_platform != "youtube":
        return []
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    repo = CardContentBlockRepository(base_dir)
    existing = repo.list_for_card(card_id)
    metadata_block = _youtube_metadata_block(card_id, raw_item, repo)
    if any(_is_current_terminal_outcome(block) for block in existing):
        return [metadata_block] if metadata_block is not None else []
    _deprecate_legacy_description_transcripts(repo, existing)
    storage_dir = resolve_data_dir(base_dir) / "assets" / card_id / "subtitles"
    attempt = fetch_youtube_subtitle(raw_item.source_url, storage_dir=storage_dir)
    outcome = dict(attempt.outcome)
    description = attempt.description or _youtube_description_fallback(raw_item)
    if description and not outcome["usable_transcript"]:
        outcome["transcript_fallback"] = "youtube_description_fallback"

    written: list[dict[str, Any]] = [metadata_block] if metadata_block is not None else []
    if outcome["usable_transcript"] is True and attempt.text.strip():
        transcript = {
            "block_id": f"block_{card_id}_transcript",
            "card_id": card_id,
            "position": _next_position(repo, card_id),
            "layer": "context",
            "block_kind": "transcript",
            "status": "enriched",
            "body": attempt.text.strip(),
            "segments": list(attempt.segments),
            "source_url": raw_item.source_url,
            "provenance": "YouTube platform subtitle",
            "raw_payload": {
                "strategy": outcome["strategy"],
                "language": outcome["language"],
                "manual_or_auto": outcome["manual_or_auto"],
                "video_id": outcome["video_id"],
                "usable_transcript": True,
                "transcript_source": "native_caption",
                "sidecar_reference": outcome["sidecar_reference"],
                "sidecar_sha256": outcome["sidecar_sha256"],
            },
        }
        repo.upsert_block(transcript)
        written.append(transcript)
    if description and outcome["usable_transcript"] is not True:
        fallback = {
            "block_id": f"block_{card_id}_youtube_description_fallback",
            "card_id": card_id,
            "position": _next_position(repo, card_id),
            "layer": "context",
            "block_kind": "enrichment",
            "status": "enriched",
            "body": description,
            "segments": [],
            "source_url": raw_item.source_url,
            "provenance": "YouTube description fallback",
            "raw_payload": {
                "strategy": "youtube_description_fallback",
                "usable": False,
                "usable_transcript": False,
            },
        }
        repo.upsert_block(fallback)
        written.append(fallback)
    outcome_block = {
        "block_id": f"block_{card_id}_youtube_subtitle_attempt",
        "card_id": card_id,
        "position": _next_position(repo, card_id),
        "layer": "context",
        "block_kind": "enrichment",
        "status": "done" if outcome["subtitle_attempt_status"] in {"usable", "unavailable"} else "blocked",
        "body": "",
        "segments": [],
        "source_url": raw_item.source_url,
        "provenance": "YouTube subtitle attempt",
        "raw_payload": outcome,
    }
    repo.upsert_block(outcome_block)
    written.append(outcome_block)
    return written


def youtube_subtitle_outcome(blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for block in blocks:
        if block["block_id"].endswith("_youtube_subtitle_attempt"):
            payload = block.get("raw_payload")
            return dict(payload) if isinstance(payload, dict) else None
    return None


def has_usable_youtube_transcript(blocks: list[dict[str, Any]]) -> bool:
    outcome = youtube_subtitle_outcome(blocks)
    if not outcome or outcome.get("subtitle_attempt_status") != "usable":
        return False
    return any(
        block["block_kind"] == "transcript"
        and block["body"].strip()
        and isinstance(block.get("raw_payload"), dict)
        and block["raw_payload"].get("usable_transcript") is True
        for block in blocks
    )


def _is_current_terminal_outcome(block: dict[str, Any]) -> bool:
    payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
    return (
        block["block_id"].endswith("_youtube_subtitle_attempt")
        and payload.get("subtitle_policy_version") == SUBTITLE_POLICY_VERSION
        and payload.get("subtitle_attempt_status") in {"usable", "unavailable", "blocked", "failed"}
    )


def _deprecate_legacy_description_transcripts(repo: Any, blocks: list[dict[str, Any]]) -> None:
    for block in blocks:
        provenance = block["provenance"].lower()
        payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        if block["block_kind"] != "transcript" or payload.get("usable_transcript") is True:
            continue
        if "description fallback" not in provenance and block["status"] != "not_found":
            continue
        repo.upsert_block(
            {
                **block,
                "block_kind": "enrichment",
                "status": "preserved" if block["body"].strip() else "not_found",
                "raw_payload": {"strategy": "legacy_youtube_description", "usable": False, "usable_transcript": False},
            }
        )


def _youtube_description_fallback(raw_item: RawItem) -> str:
    metadata = raw_item.raw_metadata
    nested = metadata.get("raw_payload") if isinstance(metadata.get("raw_payload"), dict) else {}
    record = metadata.get("opencli_record") if isinstance(metadata.get("opencli_record"), dict) else {}
    if not record and isinstance(nested.get("opencli_record"), dict):
        record = nested["opencli_record"]
    for value in (
        metadata.get("description"),
        metadata.get("snippet"),
        nested.get("description"),
        nested.get("snippet"),
        record.get("description"),
        record.get("snippet"),
    ):
        text = " ".join(str(value or "").split())
        if text:
            return text
    return ""


def _next_position(repo: Any, card_id: str) -> int:
    return max((block["position"] for block in repo.list_for_card(card_id)), default=-1) + 1


def _position_for_upsert(repo: Any, card_id: str, block_id: str) -> int:
    for block in repo.list_for_card(card_id):
        if block["block_id"] == block_id:
            return block["position"]
    return _next_position(repo, card_id)


def _youtube_metadata_block(card_id: str, raw_item: RawItem, repo: Any) -> dict[str, Any] | None:
    metadata = raw_item.raw_metadata
    nested = metadata.get("raw_payload") if isinstance(metadata.get("raw_payload"), dict) else {}
    record = metadata.get("opencli_record") if isinstance(metadata.get("opencli_record"), dict) else {}
    if not record and isinstance(nested.get("opencli_record"), dict):
        record = nested["opencli_record"]
    published_at = raw_item.raw_timestamps.published_at if raw_item.raw_timestamps else None
    values = {
        "published_at": published_at or metadata.get("published_at") or record.get("published"),
        "views": metadata.get("views", record.get("views", record.get("view_count"))),
        "likes": metadata.get("likes", record.get("likes", record.get("like_count"))),
        "duration": metadata.get("duration", record.get("duration")),
    }
    values = {key: value for key, value in values.items() if value not in (None, "")}
    if not values:
        return None
    labels = {"published_at": "发布时间", "views": "观看次数", "likes": "点赞次数", "duration": "时长"}
    block_id = f"block_{card_id}_youtube_metadata"
    block = {
        "block_id": block_id,
        "card_id": card_id,
        "position": _position_for_upsert(repo, card_id, block_id),
        "layer": "context",
        "block_kind": "metadata",
        "status": "enriched",
        "body": " · ".join(f"{labels[key]}: {value}" for key, value in values.items()),
        "segments": [],
        "source_url": raw_item.source_url,
        "provenance": "YouTube metadata via connector",
        "raw_payload": values,
    }
    repo.upsert_block(block)
    return block
