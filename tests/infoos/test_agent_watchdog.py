from backend.application.workflows.agent_watchdog import check_agent_writeback
from backend.application.usecases.submit_evaluations import submit_evaluations
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.lark_cli import prepare_directories
from backend.infrastructure.notifications.config import load_config, save_config
from backend.infrastructure.storage.agent_watchdog import AgentWatchdogRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from backend.infrastructure.storage.repositories import CardRepository
from backend.read_models.opportunities import list_opportunity_candidates
from tests.infoos.test_agent_evaluations import seed, item
from tests.infoos.test_opportunity_breakdowns import mark, save, START


def test_watchdog_threshold_dedupe_current_candidates_and_new_writeback(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    config = load_config()
    config['enabled'] = True
    config['channels']['feishu'].update(enabled=True, receive_id='ou_fixture')
    config['agent_watchdog'].update(evaluate_since=START, opportunities_since=START)
    save_config(config)
    (prepare_directories() / 'config' / 'config.json').write_text('{}')
    seed(tmp_path)
    mark(tmp_path)
    clock = '2026-09-29T01:00:00Z'
    facts = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-28T19:00:00Z', evaluate_since=START, opportunities_since=START)
    assert facts[1]['pending'] == len(list_opportunity_candidates(tmp_path, START, only_unbroken=True)['items'])
    # Backlog before the launchers' coverage floors never counts as stalled work.
    later = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-28T19:00:00Z',
        evaluate_since='2026-09-28T19:00:00Z', opportunities_since='2026-09-28T19:00:00Z')
    assert later[0]['pending'] == later[1]['pending'] == 0
    assert check_agent_writeback(tmp_path, now='2026-09-28T17:59:00Z')['notification_ids'] == []
    assert len(check_agent_writeback(tmp_path, now=clock)['notification_ids']) == 1  # opportunities >=9h; evaluate <30h
    check_agent_writeback(tmp_path, now='2026-09-30T01:00:00Z')
    check_agent_writeback(tmp_path, now='2026-09-30T02:00:00Z')
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute('select count(*) from notification_outbox').fetchone()[0] == 2
    monkeypatch.setattr('backend.application.usecases.submit_evaluations.utc_now_micro_z', lambda: '2026-09-30T02:00:00.000000Z')
    assert submit_evaluations(tmp_path, 'fixture-agent', [item(quality=10)], token_id='fixture')['results'][0]['status'] == 'accepted'
    assert save(tmp_path)[0]['status'] == 'accepted'
    facts = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-30T00:00:00Z', evaluate_since=START, opportunities_since=START)
    assert facts[0]['pending'] == facts[1]['pending'] == 0
    assert facts[0]['last_writeback'] == '2026-09-30T02:00:00.000000Z'
    with CardRepository(tmp_path).connect() as conn:
        conn.execute('update raw_items set revision=revision+1')
    facts = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-30T00:00:00Z', evaluate_since=START, opportunities_since=START)
    assert facts[1]['pending'] == len(list_opportunity_candidates(tmp_path, START, only_unbroken=True)['items']) == 1
