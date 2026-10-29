"""Read source metadata for enrichment without persisting another source copy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.raw_items import RawItemRepository


def matching_raw_item(repo: RawItemRepository, card: dict[str, Any]) -> RawItem | None:
    """Resolve a card's raw identity without scanning by source or URL."""
    payload = card.get("raw_payload")
    payload = payload if isinstance(payload, dict) else {}
    trace = payload.get("trace")
    trace = trace if isinstance(trace, dict) else {}
    raw_id = str(card.get("raw_item_id") or trace.get("raw_item_id") or "").strip()
    connector = str(card.get("raw_connector_id") or payload.get("raw_connector_id") or "").strip()
    if not raw_id:
        return None
    return repo.get_model_for_connector(connector, raw_id) if connector else repo.get_model(raw_id)


def card_with_source_payload(card: dict[str, Any], base_dir: Path | str) -> dict[str, Any]:
    """Prefer the exact raw identity; missing raw rows retain the legacy payload."""
    payload = card.get("raw_payload")
    payload = payload if isinstance(payload, dict) else {}
    nested = payload.get("raw_payload")
    metadata = nested if isinstance(nested, dict) else payload
    # A feed reconciliation deliberately retracted untrusted source body/media.
    if metadata.get("feed_content_mode") == "feed_only" and metadata.get("feed_content_origin") == "missing":
        return card
    raw = matching_raw_item(RawItemRepository(base_dir), card)
    if raw is None:
        return card
    # Card-derived quality and document status may be newer than the source row.
    overrides = {key: value for key, value in metadata.items() if key in {
        "content_quality", "content_needs_enrichment", "feed_content_origin",
        "feed_content_mode", "feed_provider", "external_doc_read",
    }}
    return {**card, "raw_payload": {**payload, "raw_payload": {
        **raw.raw_metadata, **overrides,
    }}}
