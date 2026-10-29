"""ZSXQ digest observations update metadata without restarting card processing."""
from __future__ import annotations

import sqlite3
from typing import Any

from backend.domain.models.raw_item import RawItem
from backend.domain.sources.zsxq_digest import card_is_digest, zsxq_topic_url
from backend.infrastructure.storage.base import dumps, loads


def find_zsxq_card(conn: sqlite3.Connection, item: RawItem) -> dict[str, Any] | None:
    url = zsxq_topic_url(item.source_url, item.raw_item_id)
    row = conn.execute("""select card_id, raw_payload from cards
        where source_platform = 'zsxq' and (source_url = ? or raw_item_id = ?)
        order by captured_at, card_id limit 1""", (url, item.raw_item_id)).fetchone()
    return dict(row) if row else None


def observe_zsxq_digest(conn: sqlite3.Connection, item: RawItem) -> RawItem:
    """Merge one observation in the acquisition transaction; true is sticky."""
    if item.source_platform != "zsxq":
        return item
    metadata = dict(item.raw_metadata)
    incoming = metadata.get("is_digest")
    card = find_zsxq_card(conn, item)
    old = conn.execute("""select raw_payload from raw_items
        where connector_id = ? and raw_item_id = ?""", (item.connector_id, item.raw_item_id)).fetchone()
    old_value = (loads(old["raw_payload"], {}).get("raw_metadata") or {}).get("is_digest") if old else None
    card_value = card_is_digest({"source_platform": "zsxq", "raw_payload": loads(card["raw_payload"], {})}) if card else None
    value = True if any(flag is True for flag in (incoming, old_value, card_value)) else (
        incoming if isinstance(incoming, bool) else old_value if isinstance(old_value, bool) else card_value)
    if isinstance(value, bool):
        metadata["is_digest"] = value
        item = item.model_copy(update={"raw_metadata": metadata})
        if card and value != card_value:
            payload = loads(card["raw_payload"], {})
            nested = payload.get("raw_payload")
            target = nested if isinstance(nested, dict) else payload
            target["is_digest"] = value
            conn.execute("update cards set raw_payload = ? where card_id = ?", (dumps(payload), card["card_id"]))
    return item


def zsxq_raw_observations(conn: sqlite3.Connection, item: RawItem) -> list[sqlite3.Row]:
    url = zsxq_topic_url(item.source_url, item.raw_item_id)
    return conn.execute("""select connector_id, raw_item_id, raw_payload from raw_items
        where source_platform = 'zsxq' and (raw_item_id = ? or source_url = ?)""",
        (item.raw_item_id, url)).fetchall()


def merge_digest_observation(conn: sqlite3.Connection, item: RawItem) -> bool:
    """Only touch the sticky flag; return whether the post already exists."""
    rows = zsxq_raw_observations(conn, item)
    card = find_zsxq_card(conn, item)
    if not rows and not card:
        return False
    observe_zsxq_digest(conn, item)
    for row in rows:
        payload = loads(row["raw_payload"], {})
        payload["raw_metadata"] = {**(payload.get("raw_metadata") or {}), "is_digest": True}
        conn.execute("update raw_items set raw_payload = ? where connector_id = ? and raw_item_id = ?",
                     (dumps(payload), row["connector_id"], row["raw_item_id"]))
    return True
