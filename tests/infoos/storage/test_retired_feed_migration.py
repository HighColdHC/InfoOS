from tests.infoos.storage.migration_test_helpers import drop_v21_schema
"""Historical feed data remains readable after retiring an external provider."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.application.tasks.registry import handler_for
from backend.application.tasks.repository import TaskRepository
from backend.application.workflows.auth_qr import drain_qr_rounds
from backend.application.workflows.auth_recovery import get_auth_incident
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.infrastructure.storage.database import connect_existing, default_database_path
from backend.infrastructure.storage.migrations import runner
from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository, SourceRepository
from backend.infrastructure.storage.source_config_versions import SourceConfigVersionRepository
from backend.read_models.collection_environment import _pending_auth_recoveries
from backend.read_models.source_management import read_source_page
from backend.read_models.task_manager import read_task_list

NOW = '2026-10-26T01:00:00Z'
SOURCE_IDS = ('rss_feed_905cefad46ce', 'rss_feed_e5341e11dc19')
LEGACY_POLICY = {'feed_provider': 'werss', 'feed_content_mode': 'feed_only'}


def _snapshot(conn, tables):
    return {table: [tuple(row) for row in conn.execute(f'select * from {table} order by rowid')]
            for table in tables}


@pytest.mark.parametrize('interrupt', [False, True])
def test_v19_feed_retirement_preserves_content_and_closes_login_work(tmp_path, interrupt):
    path = default_database_path(tmp_path)
    metadata = json.dumps(LEGACY_POLICY | {'auto_enrich_media': True})
    with connect_existing(path) as conn:
        for source_id in SOURCE_IDS:
            conn.execute('''insert into sources(source_id,platform,source_type,identifier,display_name,
                cadence_hours,enabled,connector_status,connector_id,raw_metadata,created_at,updated_at)
                values(?,'rss','feed','https://example.test/old.rss','Old feed',24,0,
                    'implemented_uncalibrated',?,?,?,?)''', (source_id, source_id, metadata, NOW, NOW))
            conn.execute('''insert into source_config_versions(version_id,source_id,config_version,
                fetch_input_version,config_json,change_reason,created_at) values(?,?,'old-config',
                'old-fetch',?,'created',?)''', (source_id, source_id, metadata, NOW))
            for n in range(25):
                item_id = f'{source_id}-{n}'
                conn.execute('''insert into raw_items(connector_id,raw_item_id,source_id,source_platform,
                    source_type,source_url,raw_text,captured_at,raw_payload,first_seen_at,last_seen_at)
                    values(?,?,?,'rss','article','https://example.test/article','Saved body',?,?,?,?)''',
                    (source_id, item_id, source_id, NOW, metadata, NOW, NOW))
                conn.execute('''insert into cards(card_id,source_id,source_platform,source_type,
                    source_url,title,content,captured_at,processed_at,raw_payload,raw_connector_id,
                    raw_item_id) values(?,?,'rss','article','https://example.test/article','Saved title',
                    'Saved body',?,?,?,?,?)''', (item_id, source_id, NOW, NOW, metadata, source_id, item_id))
        # The two current public feeds must keep their identity and configuration.
        for source_id in ('rss_feed_1dae3e80f5c5', 'rss_feed_f56b0a5b7924'):
            conn.execute('''insert into sources(source_id,platform,source_type,identifier,display_name,
                cadence_hours,enabled,connector_status,raw_metadata,created_at,updated_at)
                values(?,'rss','feed','https://example.test/public.rss','Public feed',24,1,
                'implemented_uncalibrated','{}',?,?)''', (source_id, NOW, NOW))
        for platform in ('wechat', 'zsxq'):
            conn.execute('''insert into auth_recovery_incidents(incident_id,platform,outage_epoch,
                reason_code,status,created_at,updated_at,notification_status) values(?,?,1,
                'platform_not_logged_in','awaiting_user',?,?,'pending')''', (platform, platform, NOW, NOW))
            conn.execute('''insert into auth_qr_rounds(incident_id,status,next_round_at,day,tab_site,
                updated_at) values(?,'waiting',?,'2026-10-26','',?)''', (platform, NOW, NOW))
            conn.execute('''insert into notification_outbox(notification_id,kind,dedupe_key,channel,
                status,title,body,related_kind,related_id,next_attempt_at,expires_at,created_at,updated_at)
                values(?,'auth_recovery_qr',?,'feishu','pending','Login','Scan','auth_incident',?,?,?,?,?)''',
                (platform, platform, platform, NOW, NOW, NOW, NOW))
            for task_type in ('auth_qr_capture', 'auth_qr_check'):
                conn.execute('''insert into tasks(task_id,task_type,target_kind,target_id,status,
                    artifacts_summary,created_at,updated_at) values(?,?,'auth_incident',?,'pending',?,?,?)''',
                    (f'{platform}-{task_type}', task_type, platform,
                     json.dumps({'queue_platform': platform, 'round_no': 0}), NOW, NOW))
        conn.execute('''insert into tasks(task_id,task_type,target_kind,target_id,status,created_at,
            updated_at) values('old-sync','werss_sync','global','external-feed','pending',?,?)''', (NOW, NOW))
        preserved = _snapshot(conn, ('raw_items', 'cards', 'source_config_versions'))
        public_before = [tuple(row) for row in conn.execute('select * from sources where enabled=1')]
        other_auth_before = tuple(conn.execute("select * from auth_recovery_incidents where platform='zsxq'").fetchone())
        drop_v21_schema(conn)
        conn.execute('pragma user_version=19')
        conn.commit()

    if interrupt:
        def fail(event):
            if event['migration'] == 20:
                raise RuntimeError('interrupted')
        with pytest.raises(RuntimeError, match='interrupted'):
            runner.migrate_database(path, progress_callback=fail)
        with connect_existing(path) as conn:
            assert conn.execute('pragma user_version').fetchone()[0] == 19
            assert json.loads(conn.execute('select raw_metadata from sources where source_id=?', SOURCE_IDS[:1]).fetchone()[0]) == json.loads(metadata)
            assert conn.execute("select status from auth_recovery_incidents where platform='wechat'").fetchone()[0] == 'awaiting_user'

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(runner.migrate_database, (path, path)))
    assert all(report.after_version == runner.LATEST_VERSION for report in reports)
    assert sum(len(report.batches) for report in reports) == runner.LATEST_VERSION - 19
    assert runner.migrate_database(path).batches == ()
    with connect_existing(path) as conn:
        assert _snapshot(conn, preserved) == preserved
        assert [tuple(row) for row in conn.execute('select * from sources where enabled=1')] == public_before
        assert tuple(conn.execute("select * from auth_recovery_incidents where platform='zsxq'").fetchone()) == other_auth_before
        assert conn.execute("select status from auth_recovery_incidents where platform='wechat'").fetchone()[0] == 'resolved'
        assert conn.execute("select status from auth_qr_rounds where incident_id='wechat'").fetchone()[0] == 'resolved'
        assert conn.execute("select status from notification_outbox where related_id='wechat'").fetchone()[0] == 'skipped'
        assert {row[0] for row in conn.execute("select status from tasks where target_id='wechat'")} == {'skipped'}
        assert conn.execute('pragma quick_check').fetchone()[0] == 'ok'
        assert conn.execute('pragma foreign_key_check').fetchall() == []
    assert read_source_page(tmp_path)['total'] == 4
    for source_id in SOURCE_IDS:
        source = SourceRepository(tmp_path).get_source(source_id)
        assert source['enabled'] is False
        connector = database_source_to_connector_config(source)
        assert (connector['feed_provider'], connector['feed_content_mode']) == ('generic', 'feed_preferred')
        assert SourceConfigVersionRepository(tmp_path).list_versions(source_id, 10)[0]['config_json'] == json.loads(metadata)
        for n in range(25):
            card = CardRepository(tmp_path).get_card(f'{source_id}-{n}')
            assert card['content'] == 'Saved body'
    assert len(RawItemRepository(tmp_path).list_raw_items()) == 50
    tasks = TaskRepository.for_base_dir(tmp_path)
    sync = tasks.get('old-sync')
    assert handler_for(sync.task_type)(sync, tmp_path)['status'] == 'skipped'
    assert len(read_task_list(tmp_path)) == 5
    assert get_auth_incident(tmp_path, 'wechat')['status'] == 'resolved'
    # The ordinary maintenance drain must ignore the now resolved historical rows.
    with connect_existing(path) as conn:
        conn.execute("update auth_recovery_incidents set status='resolved' where platform='zsxq'")
        conn.execute("update auth_qr_rounds set status='resolved' where incident_id='zsxq'")
        conn.commit()
    drain_qr_rounds(tmp_path, now=NOW)
    assert _pending_auth_recoveries(tmp_path) == []
