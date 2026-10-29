"""Reuse a ZSXQ card across maintained and research acquisition paths."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.domain.models.raw_item import RawItem
from backend.domain.card_payload import card_metadata_hash
from backend.domain.sources.zsxq_digest import card_is_digest
from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.zsxq_observations import find_zsxq_card


def reuse_zsxq_card(base_dir: Path, item: RawItem, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
    repo = CardRepository(base_dir)
    with repo.connect_existing() as conn:
        match = find_zsxq_card(conn, item)
    if not match:
        return None, False
    existing = repo.get_card(match["card_id"])
    payload["card_id"] = match["card_id"]
    if card_is_digest(existing) is True:
        payload["raw_payload"]["raw_payload"]["is_digest"] = True
    def snapshot(card: dict) -> dict:
        draft = card.get("raw_payload") or {}
        metadata = draft.get("raw_payload") or {}
        metadata_hash = draft.get("source_metadata_hash") or card_metadata_hash(item, metadata)
        return {"metadata": metadata_hash, **{key: card.get(column, draft.get(key)) for key, column in (
            ("title", "title"), ("normalized_content", "content"),
            ("source_author", "source_author"), ("published_at", "published_at"))}}
    return existing, snapshot(existing) == snapshot(payload)
