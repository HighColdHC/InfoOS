"""Read-only, indexed card entry projection shared by backfill and briefs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast
from collections.abc import Mapping, Sequence

from backend.domain.card_titles import display_title
from backend.domain.sources.zsxq_digest import card_is_digest
from backend.infrastructure.storage.base import loads
from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.entry_timestamp import entry_key
from backend.infrastructure.storage.database import connect_readonly, default_database_path


class CardEntryRow(TypedDict):
    card_id: str
    version: int
    title: str
    source_platform: str
    source_author: str | None
    published_at: str | None
    source_url: str
    entered_at: str
    is_digest: NotRequired[bool | None]


class FirstEnteredAtRow(TypedDict):
    first_seen_at: str


class DisplayBodyRow(TypedDict):
    card_id: str
    content: str
    source_platform: str
    raw_payload: object


ENTRY_KEY = "r.first_seen_at"
CARD_FIELDS = "c.card_id, c.version, c.title, c.source_platform, c.source_author, c.published_at, c.source_url"
# Start from the time-range index, then point-join active cards by raw identity.
ENTRY_JOIN = """raw_items r indexed by idx_raw_items_first_seen_source
    cross join cards c indexed by idx_cards_active_raw
    on r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id"""


def entry_window(since: str | None = None, until: str | None = None,
                 after: tuple[str, str] | None = None, platform: str | None = None) -> tuple[list[str], list[Any]]:
    clauses, params = ["c.status = 'active'"], []
    if since is not None:
        clauses.append(f"{ENTRY_KEY} >= ?")
        params.append(entry_key(since))
    if until is not None:
        clauses.append(f"{ENTRY_KEY} < ?")
        params.append(entry_key(until))
    if after is not None:
        # The explicit lower bound lets SQLite seek the time index for deep pages.
        clauses.extend([f"{ENTRY_KEY} >= ?", f"({ENTRY_KEY}, c.card_id) > (?, ?)"])
        params.extend([entry_key(after[0]), entry_key(after[0]), after[1]])
    if platform:
        clauses.append("c.source_platform = ?")
        params.append(platform)
    return clauses, params


def cards_with_entry_time(base_dir: Path | str, *, since: str | None = None, until: str | None = None,
                          after: tuple[str, str] | None = None, limit: int = 100,
                          platform: str | None = None) -> list[CardEntryRow]:
    """Return one metadata page by immutable UTC-second raw entry time."""
    clauses, params = entry_window(since, until, after, platform)
    with connect_readonly(default_database_path(base_dir)) as conn:
        rows = conn.execute(f"select {CARD_FIELDS}, {ENTRY_KEY} as entered_at from {ENTRY_JOIN} "
                            "where " + " and ".join(clauses) + f" order by {ENTRY_KEY}, c.card_id limit ?",
                            (*params, limit)).fetchall()
    return [cast(CardEntryRow, dict(row)) for row in rows]


def first_entered_at(base_dir: Path | str, card_id: str) -> str | None:
    with connect_readonly(default_database_path(base_dir)) as conn:
        row = conn.execute("""select r.first_seen_at from cards c join raw_items r
            on r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id
            where c.card_id = ? and c.status = 'active'""", (card_id,)).fetchone()
    return normalize_timestamp(cast(FirstEnteredAtRow, dict(row))['first_seen_at']) if row else None


def project_display_titles(base_dir: Path | str, rows: Sequence[Mapping[str, Any]], *, excerpt: bool = False) -> None:
    """Fetch bodies only for selected output rows; never mutate stored card inputs."""
    ids = list({row['card_id'] for row in rows})
    bodies: dict[str, DisplayBodyRow] = {}
    with connect_readonly(default_database_path(base_dir)) as conn:
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            placeholders = ','.join('?' for _ in batch)
            bodies.update((r['card_id'], cast(DisplayBodyRow, dict(r))) for r in conn.execute(
                f"select card_id, content, source_platform, raw_payload from cards where card_id in ({placeholders})", batch))
    for value in rows:
        row = cast(dict[str, Any], value)
        stored = bodies.get(row['card_id'])
        if stored is None:
            content = ''
            digest_card = {'raw_payload': loads(None, {})}
        else:
            content = stored['content']
            digest_card = {**stored, 'raw_payload': loads(stored['raw_payload'], {})}
        row['is_digest'] = card_is_digest(digest_card)
        row['title'] = display_title(row.get('source_platform', row.get('platform')), row['title'], content)
        if excerpt:
            row['excerpt'] = content[:300]
