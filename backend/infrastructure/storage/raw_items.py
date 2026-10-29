"""Raw item repository.

raw_items — module migrated to infoos in PR 4.19.
The corresponding infoos/storage/raw_items.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from backend.domain.models.raw_item import RawItem
from backend.domain.raw_item_revisions import content_snapshot, is_counter_value, raw_content_hash, same_barring_replacement
from backend.domain.timestamps import capture_timestamp, parse_utc_or_none
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now
from backend.infrastructure.storage.zsxq_observations import observe_zsxq_digest, merge_digest_observation, zsxq_raw_observations, find_zsxq_card


_COUNTER_KEYS = frozenset({
    "metrics", "public_metrics", "engagement", "engagement_metrics",
    "views", "likes", "replies", "retweets", "bookmarks", "quotes",
    "plays", "comments", "shares", "collects", "upvotes", "score", "points", "num_comments",
    "view_count", "like_count", "reply_count", "repost_count", "save_count",
    "quote_count", "comment_count", "share_count", "upvote_count",
    "favorite_count", "comments_count", "coin_count", "readers",
})


def _merge_counters(stored: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key, value in incoming.items():
        if key.casefold() in _COUNTER_KEYS and is_counter_value(key, value):
            stored[key] = value
        elif isinstance(value, dict) and isinstance(stored.get(key), dict):
            _merge_counters(stored[key], value)
        elif isinstance(value, list) and isinstance(stored.get(key), list):
            for old_item, new_item in zip(stored[key], value):
                if isinstance(old_item, dict) and isinstance(new_item, dict):
                    _merge_counters(old_item, new_item)


class RawItemRepository(InfoOSRepository):
    def upsert_raw_items(
        self,
        items: Iterable[RawItem],
        *,
        run_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        return self._upsert(items, run_id=run_id, conn=conn)[0]

    def upsert_raw_items_with_changes(
        self, items: Iterable[RawItem], *, run_id: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> list[RawItem]:
        return self._upsert(items, run_id=run_id, conn=conn)[1]

    def _upsert(
        self, items: Iterable[RawItem], *, run_id: str | None,
        conn: sqlite3.Connection | None,
    ) -> tuple[int, list[RawItem]]:
        materialized = list(items)
        if conn is not None:
            return self._upsert_in_connection(conn, materialized, run_id)
        with self.connect() as owned:
            return self._upsert_in_connection(owned, materialized, run_id)

    @staticmethod
    def _upsert_in_connection(
        conn: sqlite3.Connection, items: list[RawItem], run_id: str | None,
    ) -> tuple[int, list[RawItem]]:
        # first_seen_at uses UTC seconds (YYYY-MM-DDTHH:MM:SSZ); conflicts preserve it.
        now = utc_now()
        changed: list[RawItem] = []
        for item in items:
            metadata = dict(item.raw_metadata)
            observation = metadata.pop("_zsxq_digest_observation", False)
            only_existing = metadata.pop("_zsxq_digest_only_existing", False)
            item = item.model_copy(update={"raw_metadata": metadata})
            if item.source_platform == "zsxq" and observation:
                if merge_digest_observation(conn, item) or only_existing:
                    continue
            item = observe_zsxq_digest(conn, item)
            captured_at = capture_timestamp(item.captured_at)
            if captured_at != item.captured_at:
                item = item.model_copy(update={"captured_at": captured_at})
            raw_metadata = item.raw_metadata
            raw_author = item.raw_author.model_dump(mode="json") if item.raw_author else {}
            raw_timestamps = item.raw_timestamps.model_dump(mode="json") if item.raw_timestamps else {}
            content_hash = raw_content_hash(item)
            current = conn.execute(
                "select content_hash, revision, raw_payload from raw_items where connector_id = ? and raw_item_id = ?",
                (item.connector_id, item.raw_item_id),
            ).fetchone()
            if current is not None:
                old_item = RawItem.model_validate(loads(current["raw_payload"], {}))
                baseline = current["content_hash"] is None
                old_hash = raw_content_hash(old_item) if baseline else str(current["content_hash"])
                if baseline:
                    conn.execute(
                        "update raw_items set content_hash = ?, revision = 1 where connector_id = ? and raw_item_id = ?",
                        (old_hash, item.connector_id, item.raw_item_id),
                    )
                    RawItemRepository._insert_revision(conn, old_item, old_hash, 1, now)
                seen = conn.execute(
                    "select 1 from raw_item_revisions where connector_id = ? and raw_item_id = ? and content_hash = ?",
                    (item.connector_id, item.raw_item_id, content_hash),
                ).fetchone()
                loss_only = same_barring_replacement(content_snapshot(old_item), content_snapshot(item))
                if content_hash == old_hash or seen or loss_only:
                    payload = old_item.model_dump(mode="json")
                    _merge_counters(payload, item.model_dump(mode="json"))
                    if item.source_platform == "zsxq" and isinstance(raw_metadata.get("is_digest"), bool):
                        payload["raw_metadata"]["is_digest"] = raw_metadata["is_digest"]
                    conn.execute(
                        """update raw_items set run_id = ?, source_id = ?, last_seen_at = ?, raw_payload = ?
                           where connector_id = ? and raw_item_id = ?""",
                        (run_id, raw_metadata.get("source_id"), now, dumps(payload),
                         item.connector_id, item.raw_item_id),
                    )
                    continue
                revision = max(current["revision"], 1 if baseline else 0) + 1
            else:
                revision = 1
            conn.execute(
                """insert into raw_items (
                  connector_id, raw_item_id, source_id, run_id, source_platform, source_type,
                  source_url, source_author, title, raw_text, captured_at, published_at,
                  snapshot_ref, raw_payload, first_seen_at, last_seen_at, content_hash, revision
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(connector_id, raw_item_id) do update set
                  source_id=excluded.source_id, run_id=excluded.run_id,
                  source_platform=excluded.source_platform, source_type=excluded.source_type,
                  source_url=excluded.source_url, source_author=excluded.source_author,
                  title=excluded.title, raw_text=excluded.raw_text,
                  captured_at=excluded.captured_at, published_at=excluded.published_at,
                  snapshot_ref=excluded.snapshot_ref, raw_payload=excluded.raw_payload,
                  last_seen_at=excluded.last_seen_at, content_hash=excluded.content_hash,
                  revision=excluded.revision""",
                (item.connector_id, item.raw_item_id, raw_metadata.get("source_id"), run_id,
                 item.source_platform, item.source_type, item.source_url,
                 raw_author.get("name") or raw_author.get("handle"),
                 str(raw_metadata.get("title") or ""), item.raw_text, item.captured_at,
                 raw_timestamps.get("published_at"), item.snapshot_ref,
                 dumps(item.model_dump(mode="json")), now, now, content_hash, revision),
            )
            RawItemRepository._insert_revision(conn, item, content_hash, revision, now)
            changed.append(item)
        return len(items), changed

    @staticmethod
    def _insert_revision(conn: sqlite3.Connection, item: RawItem, content_hash: str,
                         revision: int, observed_at: str) -> None:
        conn.execute(
            """insert into raw_item_revisions
               (connector_id, raw_item_id, revision, content_hash, title, raw_text, observed_at)
               values (?, ?, ?, ?, ?, ?, ?)""",
            (item.connector_id, item.raw_item_id, revision, content_hash,
             str(item.raw_metadata.get("title") or ""), item.raw_text, observed_at),
        )

    def latest_published_at(self, source_id: str) -> str | None:
        """Compare instants, including legacy publisher offsets, without a new cursor table."""
        with self.connect_existing() as conn:
            rows = conn.execute(
                """select published_at from raw_items where source_id = ?
                   and published_at is not null and published_at != ''""", (source_id,),
            ).fetchall()
        latest = None
        latest_at = None
        for row in rows:
            published_at = row["published_at"]
            parsed = parse_utc_or_none(published_at)
            if parsed is not None and (latest_at is None or parsed > latest_at):
                latest, latest_at = published_at, parsed
        return latest

    def zsxq_topic_exists(self, item: RawItem) -> bool:
        with self.connect_existing() as conn:
            return bool(zsxq_raw_observations(conn, item) or find_zsxq_card(conn, item))

    def list_raw_items(self, *, connector_id: str | None = None) -> list[dict[str, Any]]:
        """Retained for test inspection; enrichment must use identity lookups."""
        if connector_id:
            sql = "select * from raw_items where connector_id = ? order by captured_at desc, raw_item_id"
            params = (connector_id,)
        else:
            sql = "select * from raw_items order by captured_at desc, connector_id, raw_item_id"
            params = ()
        with self.connect_existing() as conn:
            rows = conn.execute(sql, params).fetchall()
        raw_items = []
        for row in rows:
            item = dict(row)
            item["raw_payload"] = loads(item["raw_payload"], {})
            raw_items.append(item)
        return raw_items

    def list_reconciliation_page(
        self,
        *,
        after_connector_id: str | None,
        after_raw_item_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return a bounded, wrapping composite-key page for maintenance scans."""
        page_size = max(0, int(limit))
        if page_size == 0:
            return []
        connector = str(after_connector_id or "")
        raw_item = str(after_raw_item_id or "")
        with self.connect_existing() as conn:
            rows = conn.execute(
                """
                select * from raw_items
                where connector_id > ? or (connector_id = ? and raw_item_id > ?)
                order by connector_id, raw_item_id
                limit ?
                """,
                (connector, connector, raw_item, page_size),
            ).fetchall()
            if len(rows) < page_size and (connector or raw_item):
                rows.extend(
                    conn.execute(
                        """
                        select * from raw_items
                        where connector_id < ? or (connector_id = ? and raw_item_id <= ?)
                        order by connector_id, raw_item_id
                        limit ?
                        """,
                        (connector, connector, raw_item, page_size - len(rows)),
                    ).fetchall()
                )
        return [dict(row) | {"raw_payload": loads(row["raw_payload"], {})} for row in rows]

    def get(self, raw_item_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                """
                select connector_id, raw_item_id, source_id, run_id, source_platform, source_type,
                       source_url, source_author, title, raw_text, captured_at, published_at,
                       snapshot_ref, raw_payload, first_seen_at, last_seen_at
                from raw_items
                where raw_item_id = ?
                order by last_seen_at desc, connector_id
                limit 1
                """,
                (raw_item_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["raw_payload"] = loads(item["raw_payload"], {})
        return item

    def get_model(self, raw_item_id: str) -> RawItem | None:
        item = self.get(raw_item_id)
        return RawItem.model_validate(item["raw_payload"]) if item else None

    def get_for_connector(self, connector_id: str, raw_item_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                """
                select connector_id, raw_item_id, source_id, run_id, source_platform, source_type,
                       source_url, source_author, title, raw_text, captured_at, published_at,
                       snapshot_ref, raw_payload, first_seen_at, last_seen_at
                from raw_items
                where connector_id = ? and raw_item_id = ?
                """,
                (connector_id, raw_item_id),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["raw_payload"] = loads(item["raw_payload"], {})
        return item

    def get_model_for_connector(self, connector_id: str, raw_item_id: str) -> RawItem | None:
        item = self.get_for_connector(connector_id, raw_item_id)
        return RawItem.model_validate(item["raw_payload"]) if item else None
