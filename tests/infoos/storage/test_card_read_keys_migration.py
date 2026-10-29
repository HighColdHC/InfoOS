"""v15 upgrade, resumability, schema validation, and exact timestamp equivalence."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.domain.timestamps import normalize_timestamp, timestamp_sort_key
from backend.infrastructure.storage.migrations.runner import LATEST_VERSION
from backend.infrastructure.storage.migrations import DatabaseVersionTooNew, SchemaDriftError, migrate_database
from backend.infrastructure.storage.migrations.v0015_card_read_keys import PROGRESS_KEY, TRIGGERS, validate


TIMES = [None, '', 'invalid', 'pirms 7 stundām', '2026-10-01', '1784729776',
         '1784729776000', '1784729776000000', 'Tue Jul 21 20:13:46 +0000 2026',
         '2026-10-01T12:00:00+08:00', '2026-10-01T12:00:00.123456Z',
         '2026-10-01T12:00:00.1Z', '2026-10-01T12:00:00Z']


def v14_database(path, count=7):
    def stop(event):
        if event['migration'] == 15:
            raise RuntimeError('stop at v14')
    with pytest.raises(RuntimeError, match='stop at v14'):
        migrate_database(path, progress_callback=stop)
    with sqlite3.connect(path) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 14
        for n in range(count):
            key = f'{n:05d}'
            if n % 4:
                conn.execute("""insert into raw_items(connector_id,raw_item_id,source_platform,
                    source_type,source_url,captured_at,first_seen_at,last_seen_at)
                    values ('raw',?,'rss','article','','',?,'')""", (key, TIMES[(n + 2) % len(TIMES)] or ''))
            conn.execute("""insert into cards(card_id,source_platform,source_type,source_url,title,content,
                published_at,captured_at,processed_at,raw_connector_id,raw_item_id,version,content_hash)
                values (?,'rss','article','url-token','searchneedle','正文测试词',?,'','','raw',?,3,'hash')""",
                (key, TIMES[n % len(TIMES)], key))
            conn.execute("""insert into card_content_blocks(block_id,card_id,layer,block_kind,status,
                body,created_at,updated_at) values (?,?,'original','article','preserved','blockneedle','','')""", (key, key))
            conn.execute("insert into infoos_search(entity_type,entity_id,title) values ('card',?,'oldneedle')", (key,))
        conn.execute("insert into infoos_search(entity_type,entity_id,title) values ('topic','topic','topicneedle')")
    return path


def assert_backfill(path, count):
    with sqlite3.connect(path) as conn:
        conn.create_function('preview_sort_ts', 2, timestamp_sort_key)
        conn.create_function('source_timestamp', 1, normalize_timestamp)
        rows = conn.execute("""select c.sort_ts, c.published_ts,
            preview_sort_ts(c.published_at,r.first_seen_at),
            substr(replace(source_timestamp(c.published_at),'Z','') || '.000000',1,26)
            from cards c left join raw_items r
            on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id""").fetchall()
        assert len(rows) == count
        assert all((sort, published) == (old_sort, old_published) for sort, published, old_sort, old_published in rows)
        assert conn.execute('select distinct version,content_hash from cards').fetchall() == [(3, 'hash')]
        for fts, term in [('card_text_fts', 'searchneedle'), ('card_block_text_fts', 'blockneedle')]:
            assert conn.execute(f'select count(*) from {fts} where {fts} match ?', (term,)).fetchone()[0] == count
        assert conn.execute('select entity_type,entity_id from infoos_search').fetchall() == [('topic', 'topic')]
        assert not conn.execute('select 1 from settings where key=?', (PROGRESS_KEY,)).fetchall()
        assert conn.execute('pragma quick_check').fetchone()[0] == 'ok'
        assert [(r[2], r[3], r[4]) for r in conn.execute(
            'pragma index_xinfo(idx_cards_status_published)') if r[5]] == [
                ('status', 0, 'BINARY'), ('published_ts', 0, 'BINARY')]


def test_fresh_upgrade_repeat_and_batch_cap(tmp_path):
    fresh = tmp_path / 'fresh.sqlite3'
    assert migrate_database(fresh).after_version == LATEST_VERSION
    assert migrate_database(fresh).before_version == LATEST_VERSION
    with sqlite3.connect(fresh) as conn:
        validate(conn)
    path = v14_database(tmp_path / 'history.sqlite3', 1003)
    report = migrate_database(path, batch_size=900)
    assert max(e['rows'] for e in report.batches if e['migration'] == 15) <= 500
    assert_backfill(path, 1003)
    assert migrate_database(path).batches == ()
    assert_backfill(path, 1003)


@pytest.mark.parametrize('phase', ['schema', 'cards', 'blocks', 'legacy_search', 'complete'])
def test_interrupt_rolls_back_one_batch_and_resumes(tmp_path, phase):
    path = v14_database(tmp_path / 'interrupted.sqlite3')
    matching = 0
    def stop(event):
        nonlocal matching
        if event['migration'] == 15 and event['phase'] == phase:
            matching += 1
            if matching == (2 if phase in {'cards', 'blocks', 'legacy_search'} else 1):
                raise RuntimeError('interrupt batch')
    with pytest.raises(RuntimeError, match='interrupt batch'):
        migrate_database(path, batch_size=2, progress_callback=stop)
    with sqlite3.connect(path) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 14
        if phase in {'cards', 'blocks', 'legacy_search'}:
            import json
            state = json.loads(conn.execute('select value from settings where key=?', (PROGRESS_KEY,)).fetchone()[0])
            assert state['phase'] == phase
            assert state['cursor'] == (2 if phase == 'legacy_search' else '00001')
        # Successful BEGIN proves the failed migrator released its write lock.
        conn.execute('begin immediate')
    migrate_database(path, batch_size=2)
    assert_backfill(path, 7)


def test_concurrent_upgrade_and_future_version_rejection(tmp_path):
    path = v14_database(tmp_path / 'concurrent.sqlite3')
    with ThreadPoolExecutor(max_workers=3) as pool:
        reports = list(pool.map(lambda _: migrate_database(path, batch_size=2), range(3)))
    assert all(r.after_version == LATEST_VERSION for r in reports)
    assert_backfill(path, 7)
    with sqlite3.connect(path) as conn:
        conn.execute(f'pragma user_version={LATEST_VERSION + 1}')
    with pytest.raises(DatabaseVersionTooNew):
        migrate_database(path)


@pytest.mark.parametrize('statement', [
    'alter table cards drop column published_ts',
    'drop index idx_cards_status_sort',
    'drop index idx_cards_status_published',
    'drop table card_text_fts', 'drop table card_block_text_fts',
    *[f'drop trigger {name}' for name in TRIGGERS],
])
def test_v15_structure_drift_rejected(tmp_path, statement):
    path = tmp_path / 'drift.sqlite3'
    migrate_database(path)
    with sqlite3.connect(path) as conn:
        if statement == 'alter table cards drop column published_ts':
            conn.execute('drop index idx_cards_status_published')
        conn.execute(statement)
    with pytest.raises(SchemaDriftError):
        migrate_database(path)


@pytest.mark.parametrize('definition', [
    'on cards(published_ts, status)',
    'on cards(status, published_ts desc)',
    'on cards(status, published_ts collate nocase)',
    "on cards(status, published_ts) where status = 'active'",
    'on cards(status, published_ts, card_id)',
])
def test_published_index_key_drift_rejected(tmp_path, definition):
    path = tmp_path / 'index-drift.sqlite3'
    migrate_database(path)
    with sqlite3.connect(path) as conn:
        conn.execute('drop index idx_cards_status_published')
        conn.execute(f'create index idx_cards_status_published {definition}')
        with pytest.raises(SchemaDriftError, match='idx_cards_status_published'):
            validate(conn)
    with pytest.raises(SchemaDriftError):
        migrate_database(path)
