"""Read-only, bounded SQL queries for plugin card delivery."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator, Literal, cast, overload

from backend.infrastructure.storage.plugin_card_queries_types import (
    PluginAssetRow, PluginCandidateRow, PluginContentRow, PluginStableStatRow,
)

from backend.infrastructure.storage.database import connect_readonly, default_database_path



_STABLE = """c.card_type = 'information' and c.status = 'active'
    and c.version >= 1 and c.content_hash is not null
    and python_strip(c.content_hash) != ''"""
_CATALOG_COLUMNS = """c.card_id, c.card_type, c.version, c.content_hash, c.title,
    c.source_id, c.source_platform, c.published_at, c.updated_at, c.status,
    c.completeness_status, c.source_url"""
_ASSET_COLUMNS = """asset_id, card_id, asset_kind, mime_type, size_bytes,
    content_hash, status, cleanup_state, local_path"""


@contextmanager
def plugin_card_queries(base_dir: Path | str) -> Iterator[PluginCardQueries]:
    with connect_readonly(default_database_path(base_dir)) as conn:
        conn.create_function("casefold", 1, lambda v: str(v or "").casefold(), deterministic=True)
        conn.create_function("python_strip", 1, lambda v: str(v or "").strip(), deterministic=True)
        conn.create_function("asset_kind", 1, lambda v: str(v or "").strip().lower(), deterministic=True)
        # Metadata, bodies and assets in one request see the same SQLite snapshot.
        conn.execute("begin")
        yield PluginCardQueries(conn)


class PluginCardQueries:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def candidates(
        self, *, after: tuple[str, str] | None, limit: int, query: str,
        platform: str, completeness: str, media_kind: str,
        source_ids: list[str] | None, asset_states: set[str],
    ) -> list[PluginCandidateRow]:
        clauses = [_STABLE]
        params: list[str] = []
        if after is not None:
            clauses.append("(c.updated_at, c.card_id) > (?, ?)")
            params.extend(after)
        if query:
            clauses.append("""instr(casefold(coalesce(c.title, '') || ' ' ||
                coalesce(c.summary, '') || ' ' || coalesce(c.content, '') || ' ' ||
                coalesce(c.source_url, '')), ?) > 0""")
            params.append(query)
        for column, value in (("source_platform", platform), ("completeness_status", completeness)):
            if value:
                clauses.append(f"casefold(c.{column}) = ?")
                params.append(value)
        if media_kind:
            placeholders = ','.join('?' for _ in asset_states)
            clauses.append(f"""exists (select 1 from card_assets a where a.card_id = c.card_id
                and a.status in ({placeholders}) and coalesce(nullif(a.cleanup_state, ''), 'present') = 'present'
                and asset_kind(a.asset_kind) = ?)""")
            params.extend(sorted(asset_states))
            params.append(media_kind)
        # Each IN query stays within the 500-item budget, including source filters.
        batches = [None] if source_ids is None else [source_ids[i:i + 500] for i in range(0, len(source_ids), 500)]
        rows: list[PluginCandidateRow] = []
        for batch in batches:
            where = ' and '.join(clauses)
            values = list(params)
            if batch is not None:
                where += ' and c.source_id in (' + ','.join('?' for _ in batch) + ')'
                values.extend(batch)
            rows.extend(cast(PluginCandidateRow, dict(row)) for row in self.connection.execute(
                f"select {_CATALOG_COLUMNS} from cards c where {where} "
                "order by c.updated_at, c.card_id limit ?", (*values, limit)))
        if len(batches) > 1:
            rows.sort(key=lambda row: (row['updated_at'], row['card_id']))
        return rows[:limit]

    def content_for_cards(self, card_ids: list[str]) -> dict[str, PluginContentRow]:
        return {row['card_id']: row for row in self._for_cards(
            'cards', 'card_id, content, summary, raw_payload', card_ids)}

    def assets_for_cards(self, card_ids: list[str]) -> dict[str, list[PluginAssetRow]]:
        grouped: dict[str, list[PluginAssetRow]] = {card_id: [] for card_id in card_ids}
        for row in self._for_cards('card_assets', _ASSET_COLUMNS, card_ids,
                                   order='card_id, created_at, asset_id'):
            grouped[row['card_id']].append(row)
        return grouped

    @overload
    def _for_cards(self, table: Literal["cards"], columns: str, card_ids: list[str], *,
                   order: str = "card_id") -> list[PluginContentRow]: ...

    @overload
    def _for_cards(self, table: Literal["card_assets"], columns: str, card_ids: list[str], *,
                   order: str = "card_id") -> list[PluginAssetRow]: ...

    def _for_cards(self, table: str, columns: str, card_ids: list[str], *,
                   order: str = 'card_id') -> list[PluginContentRow] | list[PluginAssetRow]:
        rows: list[PluginContentRow | PluginAssetRow] = []
        for start in range(0, len(card_ids), 500):
            batch = card_ids[start:start + 500]
            placeholders = ','.join('?' for _ in batch)
            rows.extend(cast(PluginContentRow | PluginAssetRow, dict(row)) for row in self.connection.execute(
                f"select {columns} from {table} where card_id in ({placeholders}) order by {order}", batch))
        return cast(list[PluginContentRow] | list[PluginAssetRow], rows)

    def stable_card_stats(self) -> dict[str, tuple[int, str | None]]:
        rows = [cast(PluginStableStatRow, dict(row))
                for row in self.connection.execute(f"""select c.source_id, count(*) as card_count,
                    max(c.updated_at) as latest from cards c where {_STABLE}
                    and c.source_id is not null and c.source_id != '' group by c.source_id""")]
        return {row['source_id']: (row['card_count'], row['latest'] or None) for row in rows}
