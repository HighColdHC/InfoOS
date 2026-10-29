"""Deterministic input versions for durable task stages."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.feed_content import versioned_feed_policy
from backend.application.enrichment.transcripts.subtitles import SUBTITLE_POLICY_VERSION


CARD_FINALIZE_POLICY_VERSION = "card-finalize-v2"


@dataclass(frozen=True)
class InputSnapshot:
    """The immutable inputs consumed by one task stage."""

    target_kind: str
    target_id: str
    stage: str
    payload: dict[str, Any]

    def version(self) -> str:
        encoded = json.dumps(
            self.payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


def card_build_input_version(raw_item: dict[str, Any]) -> str:
    from backend.domain.sources.zsxq_digest import without_digest
    raw_item = without_digest(raw_item)
    return InputSnapshot(
        target_kind="raw_item",
        target_id=raw_item["raw_item_id"],
        stage="card_build",
        payload=raw_item,
    ).version()


def source_calibration_input_version(source: dict[str, Any]) -> str:
    """Version only source fields that change collection output.

    The historical function name remains as an internal compatibility seam;
    new task identities use the collector fetch fingerprint.
    """
    return source_fetch_input_version(source)


def enrichment_input_version(
    card: dict[str, Any],
    *,
    source: dict[str, Any] | None = None,
) -> str:
    payload = _stable_card_task_payload(card)
    if card["source_platform"] == "youtube":
        payload["subtitle_policy_version"] = SUBTITLE_POLICY_VERSION
    if source is not None:
        policy = versioned_feed_policy(
            source["platform"],
            source["source_type"],
            source["raw_metadata"],
        )
        if policy:
            payload["feed_policy"] = policy
    return InputSnapshot(
        target_kind="card",
        target_id=card["card_id"],
        stage="content_enrich",
        payload=payload,
    ).version()


def media_input_version(*, card: dict[str, Any], enrichment_version: str) -> str:
    """Version media acquisition from the durable enriched card product."""
    return _stage_version(
        "media_download",
        {"card": _stable_card_task_payload(card), "enrichment_version": enrichment_version},
    )


def asset_input_version(*, asset: dict[str, Any], media_version: str, stage: str) -> str:
    """Version media facts; processing results must not invalidate their input."""
    fields = ("asset_id", "asset_kind", "content_hash", "source_url", "mime_type", "size_bytes")
    return _stage_version(
        stage,
        {
            "policy_version": "media-derivation-v2",
            "asset": {key: asset.get(key) for key in fields},
            "media_version": media_version,
        },
    )


def video_transcript_input_version(*, audio_path: str, extract_version: str) -> str:
    """Version transcription from the exact durable/extracted audio product."""
    return _stage_version(
        "video_transcribe",
        {"audio_path": audio_path, "extract_version": extract_version},
    )


def card_finalize_input_version(
    card: dict[str, Any],
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
) -> str:
    """Version only stable, user-deliverable card facts.

    Runtime timestamps, task attempts and local absolute paths must not create a
    new finalization task for otherwise identical information.
    """
    stable_card_fields = (
        "card_id",
        "card_type",
        "source_id",
        "source_platform",
        "source_type",
        "source_url",
        "source_author",
        "title",
        "content",
        "summary",
        "published_at",
        "raw_connector_id",
        "raw_item_id",
    )
    stable_block_fields = (
        "block_id",
        "position",
        "layer",
        "block_kind",
        "status",
        "body",
        "segments",
        "source_url",
        "provenance",
    )
    stable_asset_fields = (
        "asset_id",
        "asset_kind",
        "source_url",
        "remote_url",
        "mime_type",
        "size_bytes",
        "content_hash",
        "preservation_class",
        "status",
    )
    payload = {
        "policy_version": CARD_FINALIZE_POLICY_VERSION,
        "card": {key: card.get(key) for key in stable_card_fields},
        "blocks": sorted(
            ({key: block.get(key) for key in stable_block_fields} for block in blocks),
            key=lambda item: (item["position"], item["block_id"]),
        ),
        "assets": sorted(
            ({key: asset.get(key) for key in stable_asset_fields} for asset in assets),
            key=lambda item: (item["asset_kind"], item["asset_id"]),
        ),
    }
    return _stage_version("card_finalize", payload)


def webhook_delivery_input_version(
    *, token_id: str, card_id: str, card_version: int, content_hash: str
) -> str:
    return _stage_version(
        "webhook_deliver",
        {
            "token_id": token_id,
            "card_id": card_id,
            "card_version": int(card_version),
            "content_hash": content_hash,
        },
    )


def _stage_version(stage: str, payload: dict[str, Any]) -> str:
    return InputSnapshot(
        target_kind="stage",
        target_id=stage,
        stage=stage,
        payload=payload,
    ).version()


_CARD_RUNTIME_KEYS = frozenset(
    {
        "attempt",
        "captured_at",
        "completion_token",
        "content_hash",
        "created_at",
        "finished_at",
        "last_failure_class",
        "logs",
        "missing_reasons",
        "processed_at",
        "source_run_id",
        "started_at",
        "status",
        "task_id",
        "updated_at",
        "version",
        "workflow_status",
    }
)


def _stable_card_task_payload(card: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "card_id",
        "card_type",
        "source_id",
        "source_platform",
        "source_type",
        "source_url",
        "source_author",
        "title",
        "content",
        "summary",
        "published_at",
        "raw_connector_id",
        "raw_item_id",
        "raw_payload",
    )
    payload = {key: _stable_value(card.get(key)) for key in fields}
    if card["source_platform"] == "zsxq":
        from backend.domain.sources.zsxq_digest import without_digest
        payload = without_digest(payload)
    return payload


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_value(nested)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in _CARD_RUNTIME_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return value
