"""Plain SQLite connections must maintain search without Python helpers or UDFs."""
from contextlib import closing
import json
import sqlite3

from backend.infrastructure.storage.card_text_maintenance import maintain_card_text_search

import pytest

from backend.infrastructure.storage.card_text_search import rebuild_card_text_search
from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.migrations import migrate_database
from backend.infrastructure.storage.raw_media_maintenance import maintain_raw_media, _validate
from backend.infrastructure.storage.raw_media import RawMediaStore
from tests.infoos.storage.test_card_search_writes import _card


def hits(conn, table, word):
    return {r[0] for r in conn.execute(f'select rowid from {table} where {table} match ?', (word,))}


def add_card(conn, key='c', platform='rss'):
    conn.execute("""insert into cards(card_id,source_platform,source_type,source_url,title,content,
        captured_at,processed_at) values (?,?,'article','urlneedle','titleneedle','contentneedle','','')""", (key, platform))


def add_block(conn, key='b', card='c', kind='article', provenance=''):
    conn.execute("""insert into card_content_blocks(block_id,card_id,layer,block_kind,status,body,
        provenance,created_at,updated_at) values (?,?,'original',?,'preserved','blockneedle',?,'','')""",
        (key, card, kind, provenance))


def test_card_triggers_on_plain_connections(tmp_path):
    path = tmp_path / 'plain.sqlite3'
    migrate_database(path)
    with sqlite3.connect(path) as conn:
        add_card(conn)
        rowid = conn.execute('select rowid from cards').fetchone()[0]
        for term in ['urlneedle', 'titleneedle', 'contentneedle']:
            assert hits(conn, 'card_text_fts', term) == {rowid}
        for column in ['title', 'source_author', 'content', 'source_url']:
            conn.execute(f'update cards set {column}=?', ('updatedneedle',))
            assert hits(conn, 'card_text_fts', 'updatedneedle') == {rowid}
            conn.execute(f'update cards set {column}=?', ('',))
            assert not hits(conn, 'card_text_fts', 'updatedneedle')
        conn.execute('delete from cards')
        assert conn.execute('select count(*) from card_text_fts').fetchone()[0] == 0


@pytest.mark.parametrize('platform', ['rss', 'xiaohongshu', 'douyin', 'scys', 'zsxq'])
def test_block_filters_updates_moves_and_deletion(tmp_path, platform):
    path = tmp_path / 'blocks.sqlite3'
    migrate_database(path)
    with sqlite3.connect(path) as conn:
        add_card(conn, platform=platform)
        add_card(conn, key='other', platform='rss')
        for kind in ['enrichment_gap', 'llm_context', 'media_download_gap', 'image', 'video']:
            add_block(conn, key=kind, kind=kind)
        assert not hits(conn, 'card_block_text_fts', 'blockneedle')
        add_block(conn)
        rowid = conn.execute("select rowid from card_content_blocks where block_id='b'").fetchone()[0]
        assert hits(conn, 'card_block_text_fts', 'blockneedle') == {rowid}
        conn.execute("update card_content_blocks set block_kind='linked_article', provenance=? where block_id='b'",
                     ('Generic article fetch via async enrichment',))
        assert hits(conn, 'card_block_text_fts', 'blockneedle') == ({rowid} if platform == 'rss' else set())
        conn.execute("update card_content_blocks set provenance='' where block_id='b'")
        assert hits(conn, 'card_block_text_fts', 'blockneedle') == {rowid}
        conn.execute("update card_content_blocks set provenance=?, card_id='other', body='newblockneedle' where block_id='b'",
                     ('Generic article fetch via async enrichment',))
        assert hits(conn, 'card_block_text_fts', 'newblockneedle') == {rowid}
        conn.execute("update cards set source_platform='zsxq' where card_id='other'")
        assert not hits(conn, 'card_block_text_fts', 'newblockneedle')
        conn.execute("update cards set source_platform='rss' where card_id='other'")
        assert hits(conn, 'card_block_text_fts', 'newblockneedle') == {rowid}
        conn.execute("delete from card_content_blocks where block_id='b'")
        assert not hits(conn, 'card_block_text_fts', 'newblockneedle')


@pytest.mark.parametrize('journal_mode', ['wal', 'delete'])
def test_rebuild_after_rowid_changes_and_vacuum(tmp_path, journal_mode):
    path = tmp_path / 'vacuum.sqlite3'
    migrate_database(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(f'pragma journal_mode={journal_mode}')
        add_card(conn)
        add_block(conn)
        # VACUUM may preserve rowids on this build; force relocation to test repair.
        conn.execute('update cards set rowid=101')
        conn.execute('update card_content_blocks set rowid=201')
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('vacuum')
    assert rebuild_card_text_search(path) == {'cards': 1, 'blocks': 1}
    assert rebuild_card_text_search(path) == {'cards': 1, 'blocks': 1}
    with closing(sqlite3.connect(path)) as conn, conn:
        assert hits(conn, 'card_text_fts', 'titleneedle') == {r[0] for r in conn.execute('select rowid from cards')}
        assert hits(conn, 'card_block_text_fts', 'blockneedle') == {r[0] for r in conn.execute('select rowid from card_content_blocks')}

    with closing(sqlite3.connect(path)) as conn, conn:
        for i in range(20):
            add_card(conn, key=f'deleted-{i}')
        conn.execute("update cards set content=? where card_id like 'deleted-%'", ('x' * 20000,))
        conn.execute("delete from cards where card_id like 'deleted-%'")
    before = path.read_bytes()
    keywords = ['titleneedle', 'contentneedle', 'blockneedle']
    preview = maintain_card_text_search(path, keywords=keywords)
    assert preview['mode'] == 'dry-run'
    assert preview['before']['freelist_count'] > 0
    assert path.read_bytes() == before
    result = maintain_card_text_search(path, apply=True, keywords=keywords)
    assert result['rebuilt'] == {'cards': 1, 'blocks': 1}
    assert result['integrity_check'] == ['ok']
    assert result['sample_before'] == result['sample_after'] == preview['sample_hits']
    assert result['after_vacuum']['freelist_count'] == 0
    assert result['after']['freelist_count'] < preview['before']['freelist_count']
    assert all(counts['actual'] == counts['expected'] for counts in result['after']['fts'].values())
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute('update cards set rowid=301')
        conn.execute('update card_content_blocks set rowid=401')
    repaired = maintain_card_text_search(path, apply=True, keywords=keywords, rebuild_only=True)
    assert repaired['sample_before'] is None
    assert repaired['sample_after'] == result['sample_after']

    # The raw-media stage must accept existing violations before FTS/VACUUM.
    assert maintain_raw_media(path)['foreign_key_violations_baseline'] == []
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("update cards set source_id='deleted-source', raw_payload=?", (json.dumps({
            'media_blobs': [{'url': 'https://example.feishu.cn/media.png',
                             'mime_type': 'image/png', 'base64': 'aGVsbG8='}],
        }),))
        conn.execute("""insert into auth_recovery_incidents(incident_id,platform,outage_epoch,
            reason_code,status,created_at,updated_at) values ('incident','x',1,'auth','resolved','','')""")
        conn.execute("""insert into auth_recovery_incident_tasks(incident_id,task_id,created_at)
            values ('incident','deleted-task','')""")
        baseline = {tuple(row) for row in conn.execute('pragma foreign_key_check')}
    before = path.read_bytes()
    counts = [{'table': 'auth_recovery_incident_tasks', 'parent': 'tasks', 'count': 1},
              {'table': 'cards', 'parent': 'sources', 'count': 1}]
    preview = maintain_raw_media(path)
    assert preview['foreign_key_violations_baseline'] == counts
    assert preview['tables']['cards']['inline_blobs'] == 1
    assert path.read_bytes() == before
    assert not (tmp_path / 'assets').exists()
    converted = maintain_raw_media(path, apply=True, services_stopped=True, backup_verified=True)
    assert converted['foreign_key_violations_baseline'] == counts
    assert converted['integrity_check'] == ['ok']
    assert converted['tables']['cards']['inline_blobs'] == 1
    with closing(sqlite3.connect(path)) as conn, conn:
        assert {tuple(row) for row in conn.execute('pragma foreign_key_check')} == baseline
        payload = json.loads(conn.execute('select raw_payload from cards').fetchone()[0])
        assert RawMediaStore(tmp_path).read(payload['media_blobs'][0]) == b'hello'
        assert _validate(conn, baseline) == ['ok']
        # Same grouped counts still fail when a different row violates the FK.
        conn.execute('update cards set rowid=rowid+1')
        assert len(conn.execute('pragma foreign_key_check').fetchall()) == len(baseline)
        with pytest.raises(RuntimeError, match='database integrity/foreign-key check failed; keep services stopped'):
            _validate(conn, baseline)
        # Removing existing violations without adding any is allowed.
        conn.execute('update cards set source_id=null')
        assert _validate(conn, baseline) == ['ok']


def test_repository_keys_follow_publication_and_raw_identity(tmp_path):
    repo = CardRepository(tmp_path)
    with repo.connect() as conn:
        for connector, raw, seen in [('one', 'a', '2026-01-01T00:00:00Z'), ('two', 'b', '2026-02-01T00:00:00Z')]:
            conn.execute("""insert into raw_items(connector_id,raw_item_id,source_platform,source_type,
                source_url,captured_at,first_seen_at,last_seen_at) values (?,?,'rss','article','','',?,'')""", (connector, raw, seen))
    for connector, raw, published, expected in [
        ('one', 'a', None, ('2026-01-01T00:00:00', None)),
        ('two', 'b', 'invalid', ('2026-02-01T00:00:00', None)),
        ('missing', 'a', 'invalid', ('', None)),
        ('one', 'a', '2026-03-01T00:00:00Z', ('2026-03-01T00:00:00', '2026-03-01T00:00:00.000000')),
    ]:
        repo.upsert_card(_card(raw_connector_id=connector, raw_item_id=raw, published_at=published))
        with repo.connect_existing() as conn:
            assert tuple(conn.execute('select sort_ts,published_ts from cards').fetchone()) == expected
    with repo.connect_existing() as conn:
        assert hits(conn, 'card_text_fts', 'titlecreate')
        assert not conn.execute("select 1 from infoos_search where entity_type='card'").fetchall()
