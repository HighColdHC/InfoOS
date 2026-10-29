"""T1.R2: display repairs stay outside durable card inputs."""
import sqlite3

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.input_versions import enrichment_input_version, card_finalize_input_version
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.daily_brief import brief_window
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository
from backend.application.usecases.interest_profiles import push_profile
from backend.infrastructure.storage.repositories import CardRepository
from backend.read_models.card_preview import read_card_preview_detail, read_card_preview_page
from backend.read_models.daily_brief import updates_since
from backend.read_models.plugin_cards import read_plugin_card_detail, read_plugin_card_page
from tests.infoos.test_daily_intelligence import _db, _card, PROFILE
from tests.infoos.test_intelligence_profile import _mcp

EXAMPLES = [
    ('Opus 5.', 'Opus 5.5 剪辑真的做的不错……'),
    ('OpenAI 发布 GPT-6.', 'OpenAI 发布 GPT-6.1 Sol：能力接近……'),
    ('Sonnet 5.', 'Sonnet 5.5 发布了……'),
    ('华尔街日报：OpenAI 叫停 GPT-6.', '华尔街日报：OpenAI 叫停 GPT-6.1 Astra，因为……'),
    ('RT @SpaceXAI: Grok 4.', 'RT @SpaceXAI: Grok 4.7 is now on Amazon Bedrock'),
    ('Hi,', 'Hi,\n\nTomorrow we are re-opening the Pro $200 subscriptions……'),
    ('https://t.', 'https://t.co/abc123'),
]


def _snapshot(base):
    repo = CardRepository(base)
    with repo.connect_existing() as conn:
        rows = conn.execute("select card_id from cards").fetchall()
        cards = [repo.get_card(row["card_id"], connection=conn) for row in rows]
    return {c['card_id']: (c, enrichment_input_version(c), card_finalize_input_version(c, [], [])) for c in cards}


def test_all_title_reads_and_frozen_snapshot_preserve_inputs(tmp_path):
    db = _db(tmp_path)
    push_profile(tmp_path, PROFILE, source='test')
    expected = {}
    for n, (old, body) in enumerate(EXAMPLES, 1):
        _card(db, n, '2026-09-27T12:00:00Z')
        with sqlite3.connect(db) as conn:
            conn.execute("update cards set source_platform='x', title=?, content=?, content_hash=? where card_id=?",
                         (old, body, 'sha256:' + 'a' * 64, f'card-{n}'))
        expected[f'card-{n}'] = body.split('\n\n')[-1]
    _card(db, 8, '2026-09-27T11:00:00Z', kind='paused', quality=95)
    with sqlite3.connect(db) as conn:
        conn.execute("update cards set source_platform='x', title='Opus 5.', content=?, content_hash='hash' where card_id='card-8'",
                     (EXAMPLES[0][1],))
    before = _snapshot(tmp_path)
    for cid, title in expected.items():
        assert read_card_preview_detail(tmp_path, cid)['card']['title'] == title
        assert read_plugin_card_detail(tmp_path, cid)['card']['title'] == title
    for result in (read_card_preview_page(tmp_path), read_plugin_card_page(tmp_path)):
        titles = {r['card_id']: r['title'] for r in result['items']}
        assert expected.items() <= titles.items()
    # Old snapshots remain readable even though newly built briefs use knowledge_v1.
    start, end = brief_window('2026-09-28')
    items = updates_since(tmp_path, '2026-09-27T11:59:59Z')['related']
    selected = {'items': items, 'counts': {'related': len(items), 'exploration': 0, 'landmark': 0, 'carried_over': 0},
                'weekly_paused': {'count': 1, 'top': {'title': EXAMPLES[0][1], 'deep_link': '/r/card/card-8'}}}
    repo = DailyBriefRepository(tmp_path)
    with repo.connect() as conn:
        repo.insert(conn, {'brief_date': '2026-09-28', 'window_start': start, 'window_end': end,
                           'profile_version': 1, **selected})
    brief = repo.get('2026-09-28')
    assert expected == {r['card_id']: r['title'] for r in brief['items']}
    assert brief['weekly_paused']['top']['title'] == EXAMPLES[0][1]
    assert expected == {r['card_id']: r['title'] for r in updates_since(tmp_path, '2026-09-27T11:59:59Z')['related']}
    with sqlite3.connect(db) as conn:
        frozen = conn.execute('select items_json from daily_briefs').fetchone()[0]
    assert 'Opus 5.5' in frozen and 'Tomorrow we are re-opening' in frozen
    reader = create_token(tmp_path, name='reader', scopes=['intelligence:read'])['plaintext_token']
    with TestClient(create_app(tmp_path)) as client:
        def call(name, arguments):
            result = _mcp(client, reader, name, arguments).json()['result']
            assert not result.get('isError'), result
            return result['structuredContent']
        for cid, title in expected.items():
            assert call('get_card', {'card_id': cid})['title'] == title
        for name, args in (
            ('get_cards', {'card_ids': list(expected)}),
            ('list_new_cards', {'since': '2026-09-27T12:00:00Z', 'only_unevaluated': False}),
            ('search', {'query': 'author'}),
        ):
            titles = {r['card_id']: r['title'] for r in call(name, args)['items']}
            assert expected.items() <= titles.items()
        assert expected == {r['card_id']: r['title'] for r in call('get_updates_since', {'since': '2026-09-27T11:59:59Z'})['related']}
        frozen = call('get_daily_brief', {'date': '2026-09-28'})['brief']
        assert expected == {r['card_id']: r['title'] for r in frozen['items']}
        assert frozen['weekly_paused']['top']['title'] == EXAMPLES[0][1]
    assert _snapshot(tmp_path) == before
