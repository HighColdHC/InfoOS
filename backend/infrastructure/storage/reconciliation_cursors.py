"""Persistent keyset cursors for bounded maintenance scans."""

from __future__ import annotations

from backend.infrastructure.storage.base import InfoOSRepository, utc_now


class ReconciliationCursorRepository(InfoOSRepository):
    def get(self, name: str) -> str | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                "select value from settings where key = ?",
                (self._key(name),),
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def set(self, name: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into settings(key, value, updated_at) values (?, ?, ?)
                on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at
                """,
                (self._key(name), str(value), utc_now()),
            )

    @staticmethod
    def _key(name: str) -> str:
        return f"pipeline_reconciliation.cursor.{name}"
