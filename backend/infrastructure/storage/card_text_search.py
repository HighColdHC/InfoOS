"""Explicit maintenance of rowid-based card search after VACUUM."""
from pathlib import Path
import sqlite3

from backend.infrastructure.storage.database import connect_existing, ensure_database_initialized
from backend.infrastructure.storage.migrations.v0015_card_read_keys import BLOCK_FILTER, CARD_BODY


def rebuild_card_text_search_in_connection(conn: sqlite3.Connection) -> dict[str, int]:
    """Rebuild both projections; the caller owns the transaction and commit."""
    counts = {'cards': 0, 'blocks': 0}
    for fts in ('card_text_fts', 'card_block_text_fts'):
        conn.execute(f"insert into {fts}({fts}) values ('delete-all')")
    for phase, fts, select, key in (
        ('cards', 'card_text_fts', f'select c.card_id, c.rowid, {CARD_BODY} from cards c', 'c.card_id'),
        ('blocks', 'card_block_text_fts',
         'select b.block_id, b.rowid, b.body from card_content_blocks b '
         'join cards c on c.card_id=b.card_id', 'b.block_id'),
    ):
        cursor = None
        while True:
            conditions = [BLOCK_FILTER] if phase == 'blocks' else []
            if cursor is not None:
                conditions.append(f'{key} > ?')
            where = ' where ' + ' and '.join(conditions) if conditions else ''
            rows = conn.execute(f'{select}{where} order by {key} limit 500',
                                (cursor,) if cursor is not None else ()).fetchall()
            if not rows:
                break
            conn.executemany(f'insert into {fts}(rowid,body) values (?,?)', [(r[1], r[2]) for r in rows])
            counts[phase] += len(rows)
            cursor = rows[-1][0]
    return counts


def rebuild_card_text_search(db_path: Path | str) -> dict[str, int]:
    """Atomically rebuild both indexes from current rowids; never migrate implicitly.

    Run with writers stopped after VACUUM. A failure rolls back both indexes, so
    readers cannot observe a half-built search projection.
    """
    path = ensure_database_initialized(db_path)
    with connect_existing(path) as conn:
        conn.execute('begin immediate')
        try:
            counts = rebuild_card_text_search_in_connection(conn)
            conn.commit()
            return counts
        except Exception:
            conn.rollback()
            raise
