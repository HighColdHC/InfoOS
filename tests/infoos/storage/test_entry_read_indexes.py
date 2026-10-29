"""Time boundaries, latest ordering and v8-to-v9 additive upgrade."""
import sqlite3

import pytest

from backend.application.usecases.interest_profiles import push_profile
from backend.infrastructure.storage.card_entry import cards_with_entry_time, first_entered_at
from backend.infrastructure.storage.daily_brief_query import _evaluated
from backend.infrastructure.storage.entry_timestamp import entry_key
from backend.infrastructure.storage.migrations.runner import LATEST_VERSION, migrate_database
from backend.read_models.new_cards import list_new_cards
from tests.infoos.test_daily_intelligence import _db, _card, PROFILE


@pytest.mark.parametrize('value', [
    '2026-09-28T12:00:00Z', '2026-09-28T12:00:00.1Z',
    '2026-09-28T12:00:00.999999Z', '2026-09-28T20:00:00.999999+08:00',
    '2026-09-28T07:00:00.000001-05:00', '2026-09-28T12:00:00+00:00',
])
def test_entry_boundary_is_utc_seconds(value):
    assert entry_key(value) == "2026-09-28T12:00:00Z"


def test_window_seconds_cursor_and_latest_evaluation(tmp_path):
    db = _db(tmp_path)
    push_profile(tmp_path, PROFILE, source='test')
    stamps = ['2026-09-28T12:00:00Z', '2026-09-28T12:00:00Z',
              '2026-09-28T12:00:01Z', '2026-09-28T12:00:02Z',
              '2026-09-28T12:00:03Z']
    for n, stamp in enumerate(stamps, 1):
        _card(db, n, stamp)
    args = dict(since="2026-09-28T20:00:00+08:00", until=stamps[-1], only_unevaluated=False, limit=1)
    ids, cursor = [], None
    while True:
        page = list_new_cards(tmp_path, **args, cursor=cursor)
        ids.extend(r['card_id'] for r in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert ids == ['card-1', 'card-2', 'card-3', 'card-4']
    assert [r['card_id'] for r in cards_with_entry_time(tmp_path, since=stamps[2], until=stamps[-1],
                                                     after=(stamps[2], 'card-3'), limit=1)] == ['card-4']
    assert list_new_cards(tmp_path, since=stamps[0], until=stamps[0])['items'] == []
    assert first_entered_at(tmp_path, 'missing') is None
    # Fixed six-digit UTC microseconds sort directly; equal times use evaluation_id.
    with sqlite3.connect(db) as conn:
        conn.execute("update card_evaluations set created_at='2026-09-30T02:00:00.000000Z' where card_id='card-1'")
        for eid, stamp, prompt in [('new-a', '2026-09-30T02:00:00.000001Z', 'v2'),
                                   ('new-z', '2026-09-30T02:00:00.000001Z', 'v3')]:
            conn.execute('''insert into card_evaluations (evaluation_id, card_id, card_version, profile_version,
                prompt_version, model, provider_id, quality, relevance, attribution_kind, attribution_ref, content_type,
                suggested_action, paused_landmark, summary, relation_note, passed_quality, receipt_id, created_at) select ?, card_id, card_version, profile_version,
                ?, model, provider_id, quality, relevance, attribution_kind, attribution_ref, content_type,
                suggested_action, paused_landmark, summary, relation_note, passed_quality, receipt_id, ?
                from card_evaluations where evaluation_id='eval-1' ''', (eid, prompt, stamp))
    rows = _evaluated(tmp_path, '2026-09-28T11:59:59Z', 20)
    assert next(r for r in rows if r['card_id'] == 'card-1')['evaluation_id'] == 'new-z'


def test_v9_upgrade_interruption_and_repeat_leave_card_rows_unchanged(tmp_path):
    db = tmp_path / 'v8.sqlite3'
    def stop(event):
        if event['migration'] == 9:
            raise RuntimeError('pre-v9')
    with pytest.raises(RuntimeError, match='pre-v9'):
        migrate_database(db, progress_callback=stop)
    with sqlite3.connect(db) as conn:
        assert conn.execute('pragma user_version').fetchone()[0] == 8
        assert not conn.execute("select name from sqlite_schema where name='idx_cards_active_raw'").fetchone()
    _card(db, 1, '2026-09-28T12:00:00Z', evaluated=False)
    with sqlite3.connect(db) as conn:
        before = conn.execute('select card_id, version, title, content, content_hash, updated_at from cards').fetchall()
    assert migrate_database(db).before_version == 8
    with sqlite3.connect(db) as conn:
        assert conn.execute('select card_id, version, title, content, content_hash, updated_at from cards').fetchall() == before
    with sqlite3.connect(db) as conn:
        for name in ('idx_cards_active_raw',):
            assert conn.execute('select name from sqlite_schema where name=?', (name,)).fetchone()
    assert migrate_database(db).before_version == LATEST_VERSION
