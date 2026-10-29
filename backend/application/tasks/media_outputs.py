"""Identify existing media products without using mutable task state as input."""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


def media_content_version(asset: dict[str, Any]) -> str:
    fields = ("asset_id", "asset_kind", "content_hash", "source_url", "mime_type", "size_bytes")
    payload = {key: asset.get(key) for key in fields}
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def media_output_present(asset: dict[str, Any], blocks: list[dict[str, Any]]) -> bool:
    payload = asset.get("raw_payload") or {}
    recorded = payload.get("derived_from_media_version")
    if recorded and recorded != media_content_version(asset):
        return False
    # Existing deployed outputs predate the media fingerprint. Only trust them
    # while the asset still reports a completed derivation, not after download.
    if not recorded and asset["status"] != "understood":
        return False
    if asset["asset_kind"] == "video" and payload.get("transcription_status") == "not_applicable":
        return payload.get("transcription_reason_code") in {"no_audio_stream", "no_speech_detected"}
    kinds = {"image_ocr", "image_description"} if asset["asset_kind"] == "image" else {"video_transcript"}
    asset_id = asset["asset_id"]
    return any(
        block["block_kind"] in kinds
        and block["body"].strip()
        and (
            block["source_url"] == f"asset:{asset_id}"
            or str((block.get("raw_payload") or {}).get("asset_id") or "") == asset_id
        )
        for block in blocks
    )
