"""Build the collector product's information-card draft.

This builder deliberately stops at source-normalized card content.  Scoring,
routing, topics, decisions, and other knowledge-work semantics belong outside
the InfoOS collection pipeline.
"""

from __future__ import annotations

from backend.domain.timestamps import capture_timestamp, utc_now_micro_z
from backend.domain.card_payload import compact_card_metadata, card_metadata_hash

from typing import Any

from backend.domain.models.intel_card import CardTrace, IntelCard
from backend.domain.models.normalized_item import NormalizedItem
from backend.domain.models.raw_item import RawItem
from backend.domain.card_titles import information_card_title
from backend.domain.card_identity import information_card_id


PROCESSOR_VERSION = "information-card-builder-v1"


def build_information_card(
    normalized: NormalizedItem,
    raw_item: RawItem,
    *,
    source_run_id: str | None = None,
    processed_at: str | None = None,
    first_seen_at: str | None = None,
) -> IntelCard:
    """Return an unfinalized information card with a deterministic identity."""
    raw_metadata = raw_item.raw_metadata
    source_id = _optional_text(raw_metadata.get("source_id"))
    card_id = information_card_id(raw_item.source_platform, normalized.source_url,
                                  raw_item.connector_id, raw_item.raw_item_id,
                                  source_id, raw_metadata.get("source_scope"))

    return IntelCard(
        card_id=card_id,
        card_type="information",
        source_platform=raw_item.source_platform,
        source_type=raw_item.source_type,
        source_id=source_id,
        source_lane=raw_metadata.get("source_lane"),
        source_url=normalized.source_url,
        source_author=normalized.source_author,
        discovery_reason=raw_metadata.get("discovery_reason"),
        viewpoint_hint=raw_metadata.get("viewpoint_hint"),
        source_confidence=raw_metadata.get("source_confidence"),
        published_at=normalized.published_at,
        captured_at=capture_timestamp(first_seen_at, raw_item.captured_at, processed_at),
        processed_at=processed_at or utc_now_micro_z(),
        title=information_card_title(
            raw_item.source_platform,
            normalized.title,
            normalized.normalized_content,
        ),
        normalized_content=normalized.normalized_content,
        summary=normalized.summary,
        workflow_status="processing",
        processor_version=PROCESSOR_VERSION,
        raw_connector_id=raw_item.connector_id,
        raw_item_id=raw_item.raw_item_id,
        source_run_id=source_run_id,
        trace=CardTrace(raw_item_id=raw_item.raw_item_id),
        raw_payload={
            **raw_metadata,
            **normalized.raw_metadata,
        },
    )


def information_card_payload(card: IntelCard) -> dict[str, Any]:
    """Map a draft to the CardRepository write contract."""
    metadata = card.raw_payload
    raw_payload: dict[str, Any] = {"raw_payload": compact_card_metadata(metadata)}
    raw_payload["source_metadata_hash"] = card_metadata_hash(RawItem(
        raw_item_id=card.trace.raw_item_id, connector_id=card.raw_connector_id,
        source_platform=card.source_platform, source_type=card.source_type,
        source_url=card.source_url, captured_at=card.captured_at,
    ), metadata)
    if hasattr(card, "transcript_fallback"):
        raw_payload["transcript_fallback"] = card.transcript_fallback
    return {
        "card_id": card.card_id,
        "card_type": "information",
        "source_id": card.source_id,
        "source_platform": card.source_platform,
        "source_type": card.source_type,
        "source_url": card.source_url,
        "source_author": card.source_author,
        "title": card.title,
        "content": card.normalized_content,
        "summary": card.summary,
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": card.published_at,
        "captured_at": card.captured_at,
        "processed_at": card.processed_at,
        "workflow_status": "processing",
        "status": "processing",
        "completeness_status": "processing",
        "missing_reasons": [],
        "processor_version": card.processor_version,
        "raw_connector_id": card.raw_connector_id,
        "raw_item_id": card.raw_item_id,
        "source_run_id": card.source_run_id,
        "raw_payload": raw_payload,
    }




def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
