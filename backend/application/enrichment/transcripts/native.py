from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.infrastructure.storage.repositories import CardContentBlockRepository

NATIVE_TRANSCRIPT_KINDS = {"transcript", "video_transcript"}


def write_native_transcript_block_if_available(
    base_dir: str | Path,
    asset: dict[str, Any],
) -> dict[str, Any] | None:
    repo = CardContentBlockRepository(base_dir)
    card_id = asset["card_id"]
    asset_id = asset["asset_id"]
    source_url = f"asset:{asset_id}"
    blocks = repo.list_for_card(card_id)
    existing = next(
        (
            block
            for block in blocks
            if block["block_kind"] == "video_transcript"
            and block["source_url"] == source_url
            and block["body"].strip()
        ),
        None,
    )
    if existing and _is_native_block(existing):
        return _result_from_block(existing, native=True)

    native = next((block for block in blocks if _is_native_block(block)), None)
    if not native:
        return None

    block_id = f"block_{card_id}_video_transcript_{asset_id}"
    segments = native["segments"] if isinstance(native["segments"], list) else []
    raw_payload = native["raw_payload"] if isinstance(native["raw_payload"], dict) else {}
    payload = {
        "block_id": block_id,
        "card_id": card_id,
        "position": _next_block_position(blocks),
        "layer": "derived",
        "block_kind": "video_transcript",
        "status": "enriched",
        "body": native["body"].strip(),
        "segments": segments,
        "source_url": source_url,
        "provenance": "video_transcribe",
        "raw_payload": {
            "asset_id": asset_id,
            "native_transcript_used": True,
            "native_block_id": native["block_id"],
            "language": raw_payload.get("language") or raw_payload.get("detected_language"),
            "fallback_chain": ["native_transcript"],
            "quality_flags": [],
        },
    }
    repo.upsert_block(payload)
    return {
        "status": "skipped",
        "text": payload["body"],
        "segments": segments,
        "language": payload["raw_payload"]["language"] or "",
        "language_confidence": raw_payload.get("language_confidence"),
        "provider": "native",
        "model": "native_transcript",
        "duration_seconds": raw_payload.get("duration_seconds"),
        "rtf": None,
        "fallback_chain": ["native_transcript"],
        "quality_flags": [],
        "native_transcript_used": True,
        "route": "native_transcript",
        "error": "skipped_native_transcript_used",
        "block_id": block_id,
    }


def _is_native_block(block: dict[str, Any]) -> bool:
    if block["block_kind"] not in NATIVE_TRANSCRIPT_KINDS:
        return False
    if block["status"] not in {"enriched", "preserved", "done"}:
        return False
    if not block["body"].strip():
        return False
    raw_payload = block["raw_payload"] if isinstance(block["raw_payload"], dict) else {}
    if raw_payload.get("native_transcript_used") is True:
        return True
    if block["block_kind"] == "video_transcript":
        return False
    source_url = block["source_url"] or ""
    if source_url.startswith("asset:") and "video_transcribe" in block["provenance"].lower():
        return False
    return block["block_kind"] == "transcript" and raw_payload.get("usable_transcript") is True


def _result_from_block(block: dict[str, Any], *, native: bool) -> dict[str, Any]:
    raw_payload = block["raw_payload"] if isinstance(block["raw_payload"], dict) else {}
    return {
        "status": "skipped" if native else "success",
        "text": block["body"].strip(),
        "segments": block["segments"] if isinstance(block["segments"], list) else [],
        "language": raw_payload.get("language") or "",
        "language_confidence": raw_payload.get("language_confidence"),
        "provider": "native",
        "model": "native_transcript",
        "duration_seconds": raw_payload.get("duration_seconds"),
        "rtf": None,
        "fallback_chain": ["native_transcript"],
        "quality_flags": [],
        "native_transcript_used": native,
        "route": "native_transcript",
        "error": "skipped_native_transcript_used" if native else None,
        "block_id": block["block_id"],
    }


def _next_block_position(blocks: list[dict[str, Any]]) -> int:
    return max((block["position"] for block in blocks), default=-1) + 1
