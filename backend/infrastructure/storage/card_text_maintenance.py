"""Offline VACUUM and FTS repair, never called by startup or deployment."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import sqlite3

from backend.infrastructure.storage.card_text_search import rebuild_card_text_search_in_connection
from backend.infrastructure.storage.database import connect_existing, connect_readonly, ensure_database_initialized
from backend.infrastructure.storage.migrations.v0015_card_read_keys import BLOCK_FILTER, CARD_BODY


_PROJECTIONS = (
    ('card_text_fts', 'c.card_id', 'cards c', '1', CARD_BODY),
    ('card_block_text_fts', 'b.block_id', 'card_content_blocks b join cards c on c.card_id=b.card_id',
     BLOCK_FILTER, 'b.body'),
)
_STEPS = ['stop all readers and writers; take and verify a consistent backup',
          'acquire exclusive connection', 'clear rebuildable FTS projections', 'VACUUM', 'rebuild both FTS indexes in one transaction',
          'validate rowids/counts, sampled hits, integrity_check and freelist_count',
          'resume services only after successful validation']


def _stats(conn: sqlite3.Connection) -> dict:
    result = {p: conn.execute(f'pragma {p}').fetchone()[0]
              for p in ('page_size', 'page_count', 'freelist_count', 'auto_vacuum')}
    result['free_bytes'] = result['page_size'] * result['freelist_count']
    result['fts'] = {}
    for fts, _key, source, condition, _body in _PROJECTIONS:
        result['fts'][fts] = {
            'expected': conn.execute(f'select count(*) from {source} where {condition}').fetchone()[0],
            'actual': conn.execute(f'select count(*) from {fts}').fetchone()[0],
        }
    return result


def _keywords(conn: sqlite3.Connection) -> list[str]:
    words = []
    for _fts, key, source, condition, body in _PROJECTIONS:
        rows = conn.execute(f'select {body} from {source} where {condition} order by {key} limit 20')
        for row in rows:
            for word in re.findall(r'\w{3,}', row[0] or ''):
                word = word[:16]
                if word not in words:
                    words.append(word)
                if len(words) == 3:
                    return words
    return words


def _hits(conn: sqlite3.Connection, keywords: list[str]) -> dict:
    result = {}
    for word in keywords:
        phrase = '"' + word.replace('"', '""') + '"'
        result[word] = {}
        for fts, key, source, condition, _body in _PROJECTIONS:
            alias = 'c' if fts == 'card_text_fts' else 'b'
            digest = hashlib.sha256()
            count = 0
            rows = conn.execute(f'select {key} from {source} where ({condition}) and {alias}.rowid in '
                                f'(select rowid from {fts} where {fts} match ?) order by {key}', (phrase,))
            for row in rows:
                value = row[0].encode('utf-8')
                digest.update(len(value).to_bytes(8, 'big') + value)
                count += 1
            result[word][fts] = {'count': count, 'ids_sha256': digest.hexdigest()}
    return result


def _validate(conn: sqlite3.Connection) -> list[str]:
    integrity = [row[0] for row in conn.execute('pragma integrity_check')]
    if integrity != ['ok']:
        raise RuntimeError(f'integrity_check failed: {integrity}')
    for fts, _key, source, condition, _body in _PROJECTIONS:
        alias = 'c' if fts == 'card_text_fts' else 'b'
        expected = f'select {alias}.rowid from {source} where {condition}'
        actual = f'select rowid from {fts}'
        for left, right in ((expected, actual), (actual, expected)):
            if conn.execute(f'select * from ({left} except {right}) limit 1').fetchone():
                raise RuntimeError(f'FTS rowid/count mismatch: {fts}')
    return integrity


def maintain_card_text_search(
    db_path: Path | str, *, apply: bool = False, keywords: list[str] | None = None,
    rebuild_only: bool = False,
) -> dict:
    """Preview read-only by default; apply requires an offline, backed-up database.

    EXCLUSIVE locking mode retains the database lock between VACUUM and the
    rebuild transaction, including in WAL mode. This is not a service stopper
    or backup tool. A crash after VACUUM requires repair before services resume.
    """
    path = ensure_database_initialized(db_path)
    if keywords and any(len(word) < 3 for word in keywords):
        raise ValueError('sample keywords must contain at least 3 characters (trigram FTS)')
    steps = [step for step in _STEPS if step not in ('VACUUM', 'clear rebuildable FTS projections')] if rebuild_only else _STEPS
    if not apply:
        with connect_readonly(path) as conn:
            conn.execute('begin')
            words = keywords or _keywords(conn)
            return {'mode': 'dry-run', 'db': str(path), 'steps': steps,
                    'before': _stats(conn), 'sample_hits': _hits(conn, words)}
    with connect_existing(path) as conn:
        conn.execute('pragma locking_mode=exclusive').fetchone()
        conn.execute('pragma synchronous=full')
        conn.execute('begin exclusive')
        before = _stats(conn)
        words = keywords or _keywords(conn)
        # Repair mode must also work after a crash or a manually run VACUUM.
        sample_before = None if rebuild_only else _hits(conn, words)
        if not rebuild_only:
            _validate(conn)
            # Reclaim the old index pages in this VACUUM, rather than freeing
            # them only afterwards and leaving a large new freelist.
            for fts, *_rest in _PROJECTIONS:
                conn.execute(f"insert into {fts}({fts}) values ('delete-all')")
        conn.commit()  # Exclusive mode retains the lock until this connection closes.
        if not rebuild_only:
            conn.execute('vacuum')
        after_vacuum = _stats(conn)
        conn.execute('begin immediate')
        try:
            rebuilt = rebuild_card_text_search_in_connection(conn)
            integrity = _validate(conn)
            for fts, *_rest in _PROJECTIONS:
                conn.execute(f"insert into {fts}({fts}) values ('integrity-check')")
            sample_after = _hits(conn, words)
            if sample_before is not None and sample_before != sample_after:
                raise RuntimeError('sampled stable-ID hits changed; keep services stopped')
            after = _stats(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        # Fold the completed WAL into the main file before recording file size.
        checkpoint = tuple(conn.execute('pragma wal_checkpoint(truncate)').fetchone())
        if checkpoint[0] != 0:
            raise RuntimeError(f'WAL checkpoint incomplete: {checkpoint}; keep services stopped')
        return {'mode': 'applied', 'db': str(path), 'steps': steps, 'before': before,
                'after_vacuum': after_vacuum, 'rebuilt': rebuilt, 'after': after,
                'integrity_check': integrity, 'sample_before': sample_before, 'sample_after': sample_after,
                'wal_checkpoint': checkpoint, 'database_bytes': path.stat().st_size}
