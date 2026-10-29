"""v0010: replace legacy publisher dates in captured_at with the acquisition time.

Old connectors wrote the publisher date (X strings, epoch seconds, localized
relative text, even URLs) into ``raw_items.captured_at`` and the cards built from
them. The only acquisition evidence kept for every row is
``raw_items.first_seen_at`` (UTC seconds ``Z``), so both tables take that value.
Rows without it stay as they are. Each batch walks the primary key with a cursor
in ``settings``, so an interrupted run resumes without rescanning. Versions,
content hashes and tasks are untouched: captured_at feeds neither.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from backend.domain.timestamps import captured_at_needs_repair


_PROGRESS = "_migration.v0010.{phase}.cursor"


@dataclass(frozen=True)
class BatchResult:
    phase: str
    rows: int
    repaired: int

    def as_event(self) -> dict[str, Any]:
        return {"migration": 10, "phase": self.phase, "rows": self.rows,
                "repaired": self.repaired, "complete": False}


def _cursor(conn: sqlite3.Connection, phase: str) -> Any:
    row = conn.execute("select value from settings where key = ?", (_PROGRESS.format(phase=phase),)).fetchone()
    return json.loads(row[0]) if row else None


def _save_cursor(conn: sqlite3.Connection, phase: str, value: Any) -> None:
    conn.execute(
        """insert into settings(key, value, updated_at) values (?, ?, datetime('now'))
           on conflict(key) do update set value = excluded.value, updated_at = excluded.updated_at""",
        (_PROGRESS.format(phase=phase), json.dumps(value)),
    )


def _cards(conn: sqlite3.Connection, batch_size: int) -> BatchResult | None:
    cursor = _cursor(conn, "cards")
    if cursor == "done":
        return None
    rows = conn.execute(
        """select c.card_id, c.captured_at, c.published_at, r.first_seen_at
           from cards c left join raw_items r
             on r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id
           where c.card_id > ? order by c.card_id limit ?""",
        (cursor or "", batch_size),
    ).fetchall()
    repaired = 0
    for card_id, captured, published, first_seen in rows:
        if first_seen and first_seen != captured and captured_at_needs_repair(captured, published):
            conn.execute("update cards set captured_at = ? where card_id = ? and captured_at = ?",
                         (first_seen, card_id, captured))
            repaired += 1
    _save_cursor(conn, "cards", rows[-1][0] if len(rows) == batch_size else "done")
    return BatchResult("cards", len(rows), repaired)


def _raw_items(conn: sqlite3.Connection, batch_size: int) -> BatchResult | None:
    cursor = _cursor(conn, "raw_items")
    if cursor == "done":
        return None
    after = cursor or ["", ""]
    rows = conn.execute(
        """select connector_id, raw_item_id, captured_at, published_at, first_seen_at
           from raw_items where (connector_id, raw_item_id) > (?, ?)
           order by connector_id, raw_item_id limit ?""",
        (after[0], after[1], batch_size),
    ).fetchall()
    repaired = 0
    for connector_id, raw_item_id, captured, published, first_seen in rows:
        if first_seen and first_seen != captured and captured_at_needs_repair(captured, published):
            conn.execute(
                """update raw_items set captured_at = ?
                   where connector_id = ? and raw_item_id = ? and captured_at = ?""",
                (first_seen, connector_id, raw_item_id, captured),
            )
            repaired += 1
    _save_cursor(conn, "raw_items", [rows[-1][0], rows[-1][1]] if len(rows) == batch_size else "done")
    return BatchResult("raw_items", len(rows), repaired)


def run_next_batch(conn: sqlite3.Connection, batch_size: int) -> BatchResult | None:
    """Repair one batch; ``None`` once both tables are done (progress cleared)."""
    for phase in (_cards, _raw_items):
        batch = phase(conn, batch_size)
        if batch is not None:
            return batch
    conn.execute("delete from settings where key like '_migration.v0010.%'")
    return None
