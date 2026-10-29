"""Indexed cross-source capture lookup without a schema migration.

There is no source_url index. Seek deterministic card primary keys first, then
raw primary keys within the (small) source directory. URL-valued raw IDs with
share tokens use bounded prefix ranges, never LIKE or a scan of cards/raw_items.
"""
import sqlite3
from typing import Any

from backend.domain.card_identity import information_card_id
from backend.domain.captures import capture_card_url, canonical_capture_url, capture_identity_hash, platform_item_identity
from backend.domain.capture_links import raw_identity_candidates
from backend.infrastructure.storage.base import loads

CARD_SELECT = """select c.card_id, c.status, c.title, c.source_platform, c.source_url,
    r.first_seen_at from cards c left join raw_items r
    on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id"""
CARD_BY_ID = CARD_SELECT + " where c.card_id = ? and c.status in ('active','processing')"
RAW_FIELDS = "connector_id, raw_item_id, source_url, source_platform, raw_payload, source_id"
RAW_EXACT = f"select {RAW_FIELDS} from raw_items where connector_id=? and raw_item_id=?"
RAW_RANGE = f"select {RAW_FIELDS} from raw_items where connector_id=? and raw_item_id>=? and raw_item_id<?"
ACTIVE_BY_RAW = CARD_SELECT + """ where c.status='active'
    and c.raw_connector_id=? and c.raw_item_id=? order by c.card_id limit 1"""
PENDING_CAPTURE = """select capture_id, card_id, processing_status from capture_receipts
    where canonical_identity_hash=? and processing_status in ('pending','running')
    order by created_at desc limit 1"""


def find_capture_card(conn: sqlite3.Connection, platform: str, url: str) -> dict[str, Any] | None:
    canonical = capture_card_url(platform, canonical_capture_url(url))
    identity = platform_item_identity(platform, canonical)
    sources = conn.execute(
        "select source_id, connector_id, source_scope, platform from sources",
    ).fetchall()
    # The builder hashes URLs (and source_id for maintained X), not URL columns.
    # A URL's host platform need not be its acquisition platform (e.g. RSS).
    urls = {url, canonical_capture_url(url), canonical}
    candidates = {information_card_id(platform, value, "", "") for value in urls}
    candidates.update(information_card_id("x", value, "", "", s["source_id"])
                      for s in sources if s["platform"] == "x" for value in urls)
    processing = None
    for card_id in candidates:
        card = conn.execute(CARD_BY_ID, (card_id,)).fetchone()
        if card and capture_card_url(platform, canonical_capture_url(card["source_url"])) == canonical:
            if card["status"] == "active":
                return dict(card)
            processing = dict(card)

    # Receipts retain the platform identity even when the original card URL
    # includes share tokens or the card has not finalized yet.
    for receipt in conn.execute("""select card_id from capture_receipts
            where canonical_identity_hash=? and card_id is not null order by created_at desc""",
            (capture_identity_hash(platform, canonical, extended=True),)):
        card = conn.execute(CARD_BY_ID, (receipt["card_id"],)).fetchone()
        if card:
            if card["status"] == "active":
                return dict(card)
            processing = dict(card)

    exact, prefixes = raw_identity_candidates(platform, url)
    seen = set()
    for source in sources:
        connector = source["connector_id"] or source["source_id"]
        queries = [(RAW_EXACT, (connector, value)) for value in sorted(exact)]
        queries += [(RAW_RANGE, (connector, prefix, prefix + "\uffff")) for prefix in sorted(prefixes)]
        for query, params in queries:
            for raw in conn.execute(query, params):
                key = (raw["connector_id"], raw["raw_item_id"])
                if key in seen:
                    continue
                seen.add(key)
                raw_url = canonical_capture_url(raw["source_url"])
                if capture_card_url(platform, raw_url) != canonical and (
                    not identity or platform_item_identity(platform, raw_url) != identity
                ):
                    continue
                card = conn.execute(ACTIVE_BY_RAW, key).fetchone()
                if card:
                    return dict(card)
                metadata = loads(raw["raw_payload"], {}).get("raw_metadata", {})
                card_id = information_card_id(raw["source_platform"], raw["source_url"], *key,
                                              metadata.get("source_id") or raw["source_id"],
                                              metadata.get("source_scope"))
                card = conn.execute(CARD_BY_ID, (card_id,)).fetchone()
                if card:
                    if card["status"] == "active":
                        return dict(card)
                    processing = dict(card)
    return processing


def capture_card_summary(conn: sqlite3.Connection, card: dict[str, Any]) -> dict[str, Any]:
    evaluation = conn.execute("""select quality, relevance, passed_quality, summary
        from card_evaluations where card_id=? order by created_at desc, evaluation_id desc limit 1""",
        (card["card_id"],)).fetchone()
    result = {key: card[key] for key in ("card_id", "title", "source_platform", "first_seen_at")}
    result["evaluation"] = ({**dict(evaluation), "passed_quality": bool(evaluation["passed_quality"])}
                            if evaluation else None)
    return result
