"""Frozen daily brief snapshots."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.base import InfoOSRepository, dumps, loads, utc_now


_COLUMNS = "brief_date, window_start, window_end, profile_version, items_json, generated_at"


class DailyBriefRepository(InfoOSRepository):
    @staticmethod
    def insert(conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
        """Freeze a date once in a caller-owned transaction."""
        items = payload["items"]
        if any(item.get("number") != index for index, item in enumerate(items, 1)):
            raise ValueError("brief items must have consecutive numbers starting at 1")
        try:
            if date.fromisoformat(payload["brief_date"]).isoformat() != payload["brief_date"]:
                raise ValueError
        except ValueError as exc:
            raise ValueError("brief_date must be an ISO calendar date") from exc
        start = normalize_timestamp(payload["window_start"])
        end = normalize_timestamp(payload["window_end"])
        generated = normalize_timestamp(payload.get("generated_at") or utc_now())
        if start is None or end is None or generated is None:
            raise ValueError("brief timestamps must be valid UTC ISO timestamps")
        if start >= end:
            raise ValueError("window_start must precede window_end")
        cursor = conn.execute(
            """insert into daily_briefs
               (brief_date, window_start, window_end, profile_version, items_json, generated_at)
               values (?, ?, ?, ?, ?, ?)
               on conflict(brief_date) do nothing""",
            (payload["brief_date"], start, end, payload["profile_version"],
             dumps({"items": items, "counts": payload.get("counts", {}),
                    "weekly_paused": payload.get("weekly_paused"),
                    **({"layout": payload["layout"], "stock": payload["stock"]} if "layout" in payload else {})}), generated),
        )
        return cursor.rowcount == 1

    def get(self, brief_date: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute(
                f"select {_COLUMNS} from daily_briefs where brief_date = ?", (brief_date,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        snapshot = loads(result.pop("items_json"), [])
        result.update(snapshot if isinstance(snapshot, dict) else {"items": snapshot})
        return result

    def latest(self) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            row = conn.execute("select brief_date from daily_briefs order by brief_date desc limit 1").fetchone()
        return self.get(row["brief_date"]) if row else None

    def replace(self, payload: dict[str, Any]) -> None:
        """Explicit operator rebuild; transaction keeps the old snapshot on failure."""
        with self.connect() as conn:
            conn.execute("delete from daily_briefs where brief_date = ?", (payload["brief_date"],))
            self.insert(conn, payload)
