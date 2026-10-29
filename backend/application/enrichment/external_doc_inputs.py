"""Load exact raw identity and persisted document URLs for shared routing."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.domain.external_doc_policy import content_enrich_needs_browser
from backend.infrastructure.storage.repositories import CardContentBlockRepository, RawItemRepository


def external_doc_inputs(
    card: dict[str, Any], base_dir: Path | str, *, blocks: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    raw = None
    if card.get("raw_connector_id") and card.get("raw_item_id"):
        item = RawItemRepository(base_dir).get_model_for_connector(
            card["raw_connector_id"], card["raw_item_id"])
        if item:
            raw = item.model_dump()
            card = {**card, "raw_payload": {**(card.get("raw_payload") or {}),
                                           "raw_metadata": item.raw_metadata}}
    if blocks is None:
        blocks = CardContentBlockRepository(base_dir).list_for_card(card["card_id"])
    return card, raw, blocks


def content_enrich_queue_artifacts(
    card: dict[str, Any], base_dir: Path | str, *, blocks: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    platform = card["source_platform"].strip().lower()
    if platform not in {"zsxq", "scys"}:
        return {}
    inputs = external_doc_inputs(card, base_dir, blocks=blocks)
    return {"queue_platform": platform if content_enrich_needs_browser(*inputs) else "local"}
