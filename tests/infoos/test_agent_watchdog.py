from backend.application.workflows.agent_watchdog import check_agent_writeback
from backend.application.usecases.submit_evaluations import submit_evaluations
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.lark_cli import prepare_directories
from backend.infrastructure.notifications.config import load_config, save_config
from backend.infrastructure.storage.agent_watchdog import AgentWatchdogRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from backend.infrastructure.storage.database import default_database_path
from tests.infoos.test_agent_evaluations import seed, item
from tests.infoos.test_daily_intelligence import _card

START = "2026-09-28T12:00:00Z"


def test_watchdog_threshold_dedupe_and_new_writeback(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    config = load_config()
    config['enabled'] = True
    config['channels']['feishu'].update(enabled=True, receive_id='ou_fixture')
    config['agent_watchdog'].update(evaluate_since=START, evaluate_stall_hours=9)
    save_config(config)
    (prepare_directories() / 'config' / 'config.json').write_text('{}')
    seed(tmp_path)
    facts = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-28T19:00:00Z', evaluate_since=START)
    assert len(facts) == 1 and facts[0]['job'] == 'evaluate' and facts[0]['pending'] == 1
    # Backlog before the launcher's coverage floor never counts as stalled work.
    later = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-28T19:00:00Z',
        evaluate_since='2026-09-28T19:00:00Z')
    assert later[0]['pending'] == 0
    assert check_agent_writeback(tmp_path, now='2026-09-28T20:59:00Z')['notification_ids'] == []
    assert len(check_agent_writeback(tmp_path, now='2026-09-28T21:00:00Z')['notification_ids']) == 1
    check_agent_writeback(tmp_path, now='2026-09-29T01:00:00Z')
    monkeypatch.setattr('backend.application.usecases.submit_evaluations.utc_now_micro_z', lambda: '2026-09-30T02:00:00.000000Z')
    assert submit_evaluations(tmp_path, 'fixture-agent', [item(quality=10)], token_id='fixture')['results'][0]['status'] == 'accepted'
    facts = AgentWatchdogRepository(tmp_path).facts(older_than='2026-09-30T00:00:00Z', evaluate_since=START)
    assert facts[0]['pending'] == 0
    assert facts[0]['last_writeback'] == '2026-09-30T02:00:00.000000Z'
    _card(default_database_path(tmp_path), 2, '2026-09-30T02:00:00Z', evaluated=False)
    assert check_agent_writeback(tmp_path, now='2026-09-30T10:59:00Z')['notification_ids'] == []
    assert len(check_agent_writeback(tmp_path, now='2026-09-30T11:00:00Z')['notification_ids']) == 1
    check_agent_writeback(tmp_path, now='2026-09-30T12:00:00Z')
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute('select count(*) from notification_outbox').fetchone()[0] == 2
