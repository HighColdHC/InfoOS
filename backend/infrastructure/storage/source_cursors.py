"""SQLite-backed source cursor persistence."""

from __future__ import annotations

import sqlite3
from typing import Any

from backend.infrastructure.storage.base import InfoOSRepository, utc_now


class SourceCursorRepository(InfoOSRepository):
    """Persist one connector cursor for each configured source."""

    def get(self, source_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                """
                select source_id, connector_id, cursor_value, last_run_id, updated_at
                from source_cursors
                where source_id = ?
                """,
                (source_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert(
        self,
        *,
        source_id: str,
        connector_id: str,
        cursor_value: str | None,
        last_run_id: str | None,
        updated_at: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        payload = {
            "source_id": source_id,
            "connector_id": connector_id,
            "cursor_value": cursor_value,
            "last_run_id": last_run_id,
            "updated_at": updated_at or utc_now(),
        }
        if conn is not None:
            self._upsert(conn, payload)
            return
        with self.connect() as own_conn:
            self._upsert(own_conn, payload)

    @staticmethod
    def _upsert(conn: sqlite3.Connection, payload: dict[str, str | None]) -> None:
        conn.execute(
            """
            insert into source_cursors (
              source_id, connector_id, cursor_value, last_run_id, updated_at
            ) values (
              :source_id, :connector_id, :cursor_value, :last_run_id, :updated_at
            )
            on conflict(source_id) do update set
              connector_id=excluded.connector_id,
              cursor_value=excluded.cursor_value,
              last_run_id=excluded.last_run_id,
              updated_at=excluded.updated_at
            """,
            payload,
        )
