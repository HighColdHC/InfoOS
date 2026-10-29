"""v0015: materialized timestamps and trigger-maintained trigram indexes."""
from __future__ import annotations

import json
import re
import sqlite3

from backend.infrastructure.storage.card_read_keys import card_read_keys
from .v0001_baseline import SchemaDriftError


PROGRESS_KEY = '_migration.v0015.progress'
CARD_BODY = ("coalesce(c.title,'') || char(10) || coalesce(c.source_author,'') || char(10)"
             " || coalesce(c.content,'') || char(10) || coalesce(c.source_url,'')")
BLOCK_FILTER = """b.block_kind not in ('enrichment_gap','llm_context','media_download_gap','image','video')
    and not (c.source_platform in ('xiaohongshu','douyin','scys','zsxq')
      and b.block_kind = 'linked_article'
      and b.provenance = 'Generic article fetch via async enrichment')"""


def _triggers() -> dict[str, str]:
    triggers = {}
    for table, fts, columns, insert in (
        ('cards', 'card_text_fts', 'title, source_author, content, source_url',
         f'insert into card_text_fts(rowid, body) values (new.rowid, {CARD_BODY.replace("c.", "new.")});'),
        ('card_content_blocks', 'card_block_text_fts', 'body, block_kind, provenance, card_id',
         'insert into card_block_text_fts(rowid, body) '
         'select new.rowid, new.body from cards c where c.card_id = new.card_id and '
         + BLOCK_FILTER.replace('b.', 'new.') + ';'),
    ):
        for action in ('insert', 'update', 'delete'):
            name = f'{fts}_after_{action}'
            event = f'update of {columns}' if action == 'update' else action
            body = f'delete from {fts} where rowid = old.rowid;' if action != 'insert' else ''
            if action != 'delete':
                body += insert
            triggers[name] = f'create trigger {name} after {event} on {table} begin {body} end;'
    # Platform is part of block eligibility, and an upsert can change it.
    name = 'card_block_text_fts_after_platform_update'
    triggers[name] = f"""create trigger {name} after update of source_platform on cards
        when old.source_platform is not new.source_platform begin
          delete from card_block_text_fts where rowid in
            (select rowid from card_content_blocks where card_id = new.card_id);
          insert into card_block_text_fts(rowid, body)
            select b.rowid, b.body from card_content_blocks b join cards c on c.card_id = b.card_id
            where c.card_id = new.card_id and {BLOCK_FILTER};
        end;"""
    return triggers


TRIGGERS = _triggers()
STATEMENTS = [
    "alter table cards add column sort_ts text not null default '';",
    'alter table cards add column published_ts text;',
    'create index idx_cards_status_sort on cards(status, sort_ts desc, card_id desc);',
    'create index idx_cards_status_published on cards(status, published_ts);',
    "create virtual table card_text_fts using fts5(body, tokenize='trigram', content='', contentless_delete=1);",
    "create virtual table card_block_text_fts using fts5(body, tokenize='trigram', content='', contentless_delete=1);",
    *TRIGGERS.values(),
]


def _trigger_tokens(sql: str) -> list[str]:
    return [token if token.startswith(("'", '\"')) else token.lower()
            for token in re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\w+|[^\s]", sql.rstrip(';'))]


def validate(conn: sqlite3.Connection) -> None:
    """Validate ordering directions and triggers beyond baseline column/FTS checks."""
    indexes = {r[1]: r for r in conn.execute('pragma index_list(cards)')}
    for name, expected in {
        'idx_cards_status_sort': [('status', 0, 'BINARY'), ('sort_ts', 1, 'BINARY'), ('card_id', 1, 'BINARY')],
        'idx_cards_status_published': [('status', 0, 'BINARY'), ('published_ts', 0, 'BINARY')],
    }.items():
        keys = [(r[2], r[3], r[4]) for r in conn.execute(f'pragma index_xinfo({name})') if r[5]]
        index = indexes.get(name)
        if keys != expected or index is None or index[2] or index[4]:
            raise SchemaDriftError(f'index drift for {name}')
    for name, expected in TRIGGERS.items():
        row = conn.execute("select sql from sqlite_schema where type='trigger' and name=?", (name,)).fetchone()
        if row is None or _trigger_tokens(row[0]) != _trigger_tokens(expected):
            raise SchemaDriftError(f'trigger drift for {name}')


def _save(conn: sqlite3.Connection, phase: str, cursor: str | int | None) -> None:
    conn.execute("""insert into settings(key,value,updated_at) values (?,?,datetime('now'))
        on conflict(key) do update set value=excluded.value, updated_at=excluded.updated_at""",
        (PROGRESS_KEY, json.dumps({'phase': phase, 'cursor': cursor})))


def has_progress(conn: sqlite3.Connection) -> bool:
    return conn.execute('select 1 from settings where key=?', (PROGRESS_KEY,)).fetchone() is not None


def _cards(conn: sqlite3.Connection, cursor: str | None, limit: int) -> list:
    rows = conn.execute(f"""select c.card_id, c.rowid, c.published_at, r.first_seen_at, {CARD_BODY}
        from cards c left join raw_items r
          on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id
        {'where c.card_id > ?' if cursor is not None else ''}
        order by c.card_id limit ?""", (cursor, limit) if cursor is not None else (limit,)).fetchall()
    for card_id, rowid, published, seen, body in rows:
        conn.execute('update cards set sort_ts=?, published_ts=? where card_id=?',
                     (*card_read_keys(published, seen), card_id))
        conn.execute('delete from card_text_fts where rowid=?', (rowid,))
        conn.execute('insert into card_text_fts(rowid,body) values (?,?)', (rowid, body))
    return rows


def _blocks(conn: sqlite3.Connection, cursor: str | None, limit: int) -> list:
    rows = conn.execute(f"""select b.block_id, b.rowid, b.body, ({BLOCK_FILTER}) eligible
        from card_content_blocks b join cards c on c.card_id=b.card_id
        {'where b.block_id > ?' if cursor is not None else ''}
        order by b.block_id limit ?""", (cursor, limit) if cursor is not None else (limit,)).fetchall()
    for _block_id, rowid, body, eligible in rows:
        conn.execute('delete from card_block_text_fts where rowid=?', (rowid,))
        if eligible:
            conn.execute('insert into card_block_text_fts(rowid,body) values (?,?)', (rowid, body))
    return rows


def run_next_batch(conn: sqlite3.Connection, batch_size: int) -> dict:
    """Caller owns BEGIN IMMEDIATE, version reread, and atomic batch commit."""
    batch_size = min(batch_size, 500)
    row = conn.execute('select value from settings where key=?', (PROGRESS_KEY,)).fetchone()
    if row is None:
        for statement in STATEMENTS:
            conn.execute(statement)
        _save(conn, 'cards', None)
        return dict(migration=15, phase='schema', rows=0, complete=False)
    state = json.loads(row[0])
    phase, cursor = state['phase'], state['cursor']
    if phase == 'cards':
        rows = _cards(conn, cursor, batch_size)
        next_phase = 'blocks'
    elif phase == 'blocks':
        rows = _blocks(conn, cursor, batch_size)
        next_phase = 'legacy_search'
    elif phase == 'legacy_search':
        # Walk all legacy rows once; filtering entity_type itself cannot use FTS.
        rows = conn.execute(f"""select rowid, entity_type from infoos_search
            {'where rowid > ?' if cursor is not None else ''} order by rowid limit ?""",
            (cursor, batch_size) if cursor is not None else (batch_size,)).fetchall()
        conn.executemany('delete from infoos_search where rowid=?', [(r[0],) for r in rows if r[1] == 'card'])
        next_phase = 'complete'
    elif phase == 'complete':
        validate(conn)
        conn.execute('delete from settings where key=?', (PROGRESS_KEY,))
        return dict(migration=15, phase='complete', rows=0, complete=True)
    else:
        raise SchemaDriftError(f'invalid v0015 progress phase: {phase!r}')
    _save(conn, phase if len(rows) == batch_size else next_phase,
          rows[-1][0] if len(rows) == batch_size else None)
    return dict(migration=15, phase=phase, rows=len(rows), cursor=rows[-1][0] if rows else cursor, complete=False)
