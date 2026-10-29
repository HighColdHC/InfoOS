"""Topic repository.

topics — module migrated to infoos in PR 4.25.
The corresponding infoos/storage/topics.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository, dumps, utc_now


class TopicRepository(InfoOSRepository):
    def upsert_topic(self, payload: dict[str, Any]) -> None:
        with self.connect() as conn:
            self._upsert_topic(conn, payload)
            conn.commit()

    def attach_card(self, topic_id: str, card_id: str) -> None:
        with self.connect() as conn:
            self._attach_card(conn, topic_id, card_id, position=0)
            conn.commit()

    def upsert_topic_with_cards(self, payload: dict[str, Any], *, card_ids: list[str]) -> None:
        """Persist one Topic and its ordered membership in one transaction."""
        with self.connect() as conn:
            self._upsert_topic(conn, payload)
            for position, card_id in enumerate(card_ids):
                self._attach_card(conn, str(payload["topic_id"]), card_id, position=position)
            conn.commit()

    def topic_ids_for_cards(self, card_ids: list[str]) -> set[str]:
        """Return every durable topic currently containing one of the cards."""
        if not card_ids:
            return set()
        placeholders = ", ".join("?" for _ in card_ids)
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"select distinct topic_id from topic_cards where card_id in ({placeholders})",
                card_ids,
            ).fetchall()
        return {str(row["topic_id"]) for row in rows}

    def rebuild_and_replace_card_memberships(
        self,
        *,
        processed_card_ids: list[str],
        current_topic_ids: set[str],
        rebuild: Callable[[set[str], dict[str, list[str]]], list[tuple[dict[str, Any], list[str]]]],
    ) -> None:
        """Rebuild and replace memberships under one serialized SQLite write.

        A TOPIC_UPDATE must derive its aggregate from the same membership
        snapshot it subsequently replaces.  Taking ``BEGIN IMMEDIATE`` before
        reading that snapshot prevents an older overlapping task from
        reattaching a card another task has just moved to a different topic.
        """
        with self.connect() as conn:
            self._begin_membership_rebuild(conn)
            affected_topic_ids = self._topic_ids_for_cards_in_connection(conn, processed_card_ids) | current_topic_ids
            member_ids_by_topic = {
                topic_id: self._card_ids_for_topic_in_connection(conn, topic_id)
                for topic_id in affected_topic_ids
            }
            topic_payloads = rebuild(affected_topic_ids, member_ids_by_topic)
            if processed_card_ids:
                placeholders = ", ".join("?" for _ in processed_card_ids)
                conn.execute(
                    f"delete from topic_cards where card_id in ({placeholders})",
                    processed_card_ids,
                )
            for payload, card_ids in topic_payloads:
                self._upsert_topic(conn, payload)
                for position, card_id in enumerate(card_ids):
                    self._attach_card(conn, str(payload["topic_id"]), card_id, position=position)
            conn.commit()

    @staticmethod
    def _begin_membership_rebuild(conn: Any) -> None:
        conn.execute("begin immediate")

    @staticmethod
    def _topic_ids_for_cards_in_connection(conn: Any, card_ids: list[str]) -> set[str]:
        if not card_ids:
            return set()
        placeholders = ", ".join("?" for _ in card_ids)
        rows = conn.execute(
            f"select distinct topic_id from topic_cards where card_id in ({placeholders})",
            card_ids,
        ).fetchall()
        return {str(row["topic_id"]) for row in rows}

    @staticmethod
    def _card_ids_for_topic_in_connection(conn: Any, topic_id: str) -> list[str]:
        rows = conn.execute(
            "select card_id from topic_cards where topic_id = ? order by position, created_at",
            (topic_id,),
        ).fetchall()
        return [str(row["card_id"]) for row in rows]

    def card_ids_for_topic(self, topic_id: str) -> list[str]:
        with self.connect_existing() as conn:
            rows = conn.execute(
                "select card_id from topic_cards where topic_id = ? order by position, created_at",
                (topic_id,),
            ).fetchall()
        return [row["card_id"] for row in rows]

    def list_topics(self, *, domain_id: str | None = None) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if domain_id:
            where = "where primary_domain_id = ?"
            params.append(domain_id)
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"select * from topics {where} order by updated_at desc, topic_id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _upsert_topic(conn: Any, payload: dict[str, Any]) -> None:
        now = utc_now()
        created_at = payload.get("created_at", now)
        updated_at = payload.get("updated_at", now)
        conn.execute(
            """
            insert into topics (
              topic_id, title, latest_progress, ai_interpretation, primary_domain_id,
              secondary_domain_ids, status, updated_at, created_at, source_summary, raw_payload
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(topic_id) do update set
              title=excluded.title,
              latest_progress=excluded.latest_progress,
              ai_interpretation=excluded.ai_interpretation,
              primary_domain_id=excluded.primary_domain_id,
              secondary_domain_ids=excluded.secondary_domain_ids,
              status=excluded.status,
              updated_at=excluded.updated_at,
              source_summary=excluded.source_summary,
              raw_payload=excluded.raw_payload
            """,
            (
                payload["topic_id"], payload["title"], payload.get("latest_progress", ""),
                payload.get("ai_interpretation", ""), payload.get("primary_domain_id"),
                dumps(payload.get("secondary_domain_ids", [])), payload.get("status", "刚出现"),
                updated_at, created_at, payload.get("source_summary", ""), dumps(payload.get("raw_payload", {})),
            ),
        )

    @staticmethod
    def _attach_card(conn: Any, topic_id: str, card_id: str, *, position: int) -> None:
        conn.execute(
            """
            insert into topic_cards (topic_id, card_id, position, created_at)
            values (?, ?, ?, ?)
            on conflict(topic_id, card_id) do update set position=excluded.position
            """,
            (topic_id, card_id, position, utc_now()),
        )
