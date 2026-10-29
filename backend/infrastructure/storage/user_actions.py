"""Durable SQLite storage for user feedback and locally tracked actions."""

from __future__ import annotations

import sqlite3
from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


class UserActionRepository(InfoOSRepository):
    def upsert_action(
        self,
        payload: dict[str, Any],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> dict[str, Any]:
        """Create or replace a durable user action and return its decoded row."""
        now = utc_now()
        created_at = str(payload.get("created_at") or now)
        updated_at = str(payload.get("updated_at") or now)
        if conn is not None:
            return self._upsert_action(conn, payload, created_at=created_at, updated_at=updated_at)
        with self.connect() as own_conn:
            return self._upsert_action(own_conn, payload, created_at=created_at, updated_at=updated_at)

    def _upsert_action(
        self,
        conn: sqlite3.Connection,
        payload: dict[str, Any],
        *,
        created_at: str,
        updated_at: str,
    ) -> dict[str, Any]:
        conn.execute(
            """
            insert into user_actions(
              action_id, card_id, topic_id, decision_id, artifact_id, action,
              status, created_at, updated_at, details
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(action_id) do update set
              card_id=excluded.card_id,
              topic_id=excluded.topic_id,
              decision_id=excluded.decision_id,
              artifact_id=excluded.artifact_id,
              action=excluded.action,
              status=excluded.status,
              updated_at=excluded.updated_at,
              details=excluded.details
            """,
            (
                payload["action_id"],
                payload.get("card_id"),
                payload.get("topic_id"),
                payload.get("decision_id"),
                payload.get("artifact_id"),
                payload["action"],
                payload.get("status", "recorded"),
                created_at,
                updated_at,
                dumps(payload.get("details", {})),
            ),
        )
        row = conn.execute(
            "select * from user_actions where action_id = ?", (payload["action_id"],)
        ).fetchone()
        if row is None:  # pragma: no cover - defensive against external deletion.
            raise RuntimeError("stored user action could not be read")
        return self._decode(dict(row))

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select * from user_actions where action_id = ?", (action_id,)
            ).fetchone()
        return self._decode(dict(row)) if row else None

    def list_actions(
        self,
        *,
        card_id: str | None = None,
        topic_id: str | None = None,
        decision_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("card_id", card_id), ("topic_id", topic_id), ("decision_id", decision_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = " where " + " and ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 1000)))
        with self.connect_existing() as conn:
            rows = conn.execute(
                f"select * from user_actions{where} order by created_at, action_id limit ?",
                tuple(params),
            ).fetchall()
        return [self._decode(dict(row)) for row in rows]

    def delete(self, action_id: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute("delete from user_actions where action_id = ?", (action_id,))
        return cursor.rowcount > 0

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        return row | {"details": loads(row["details"], {})}
