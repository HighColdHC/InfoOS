"""Drive QR rounds with fake browser actions and a persistent SQLite store."""
from datetime import timedelta
import sqlite3

import pytest

from backend.application.tasks.handlers import auth_qr as handlers
from backend.application.tasks.repository import TaskRepository
from backend.application.workflows.auth_qr import start_qr_round, check_round
from backend.domain.tasks import Task, TaskType
from backend.domain.timestamps import parse_utc, format_utc_seconds_z
from backend.infrastructure.browser.qr_capture import QrCaptureError
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.lark_cli import prepare_directories
from backend.infrastructure.notifications.config import load_config, save_config
from backend.infrastructure.storage.auth_qr import AuthQrRepository
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository

NOW = '2026-10-21T01:00:00Z'


@pytest.fixture
def qr_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    config = load_config()
    config['enabled'] = True
    config['channels']['feishu'].update(enabled=True, receive_id='ou_fixture')
    save_config(config)
    (prepare_directories() / 'config' / 'config.json').write_text('{}')
    tasks = TaskRepository.for_base_dir(tmp_path)
    tasks.enqueue(Task('fetch-qr', TaskType.FETCH, 'source', 'synthetic'))
    incident, _ = AuthRecoveryRepository(tmp_path).create_or_link(platform='xiaohongshu', outage_epoch=1,
        reason_code='platform_not_logged_in', task_id='fetch-qr', occurred_at=NOW)
    closed, paths = [], []
    def capture(platform, site, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fake-qr')
        path.chmod(0o600)
        paths.append(path)
        return 'synthetic-lease'
    monkeypatch.setattr(handlers, 'capture_qr', capture)
    monkeypatch.setattr(handlers, 'verify_auth_recovery', lambda *a: {
        'ok': False, 'verification': {'code': 'platform_session_not_ready', 'login_required': True}})
    monkeypatch.setattr('backend.application.workflows.auth_qr.close_qr_tab', lambda site: closed.append(site))
    monkeypatch.setattr('backend.application.workflows.auth_qr.login_cookie_changed_since', lambda *a: False)
    return incident, tasks, paths, closed


def _capture(tmp_path, tasks, incident_id, round_no, code, monkeypatch, now):
    task = tasks.get(f'qrcapture_{incident_id}_{round_no}_{code}')
    assert task is not None
    monkeypatch.setattr(handlers, 'current_iso', lambda: now)
    return handlers.handle_auth_qr_capture(task, tmp_path)


def test_three_codes_expire_round_next_round_and_restart(tmp_path, monkeypatch, qr_setup):
    incident, tasks, paths, closed = qr_setup
    incident_id = incident['incident_id']
    first = start_qr_round(tmp_path, incident_id, now=NOW)
    assert first['round_no'] == 1
    assert start_qr_round(tmp_path, incident_id, manual=True, now=NOW)['round_no'] == 1
    repo = AuthQrRepository(tmp_path)
    now = NOW
    for code in range(1, 4):
        assert _capture(tmp_path, tasks, incident_id, 1, code, monkeypatch, now)['status'] == 'success'
        row = repo.get(incident_id)
        assert row['codes_sent'] == code
        assert paths[-1].stat().st_mode & 0o777 == 0o600
        # Recreate repository/handler as a restarted worker, replaying the same completed capture.
        before = len(paths)
        assert _capture(tmp_path, tasks, incident_id, 1, code, monkeypatch, now)['status'] == 'skipped'
        assert len(paths) == before
        now = row['code_expires_at']
        check_round(tmp_path, incident_id, now=now)
        assert not paths[-1].exists()
    row = AuthQrRepository(tmp_path).get(incident_id)
    assert row['status'] == 'idle'
    assert len(closed) == 1
    second = start_qr_round(tmp_path, incident_id, now=row['next_round_at'])
    assert second['round_no'] == 2 and second['codes_sent'] == 0
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute("select count(*) from notification_outbox where kind='auth_recovery_qr'").fetchone()[0] == 3
        assert conn.execute("select count(*) from notification_outbox where kind='auth_recovery_text'").fetchone()[0] == 1


def test_login_functional_verification_and_manual_resolution_cleanup(tmp_path, monkeypatch, qr_setup):
    incident, tasks, paths, closed = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)
    issued_at = []
    monkeypatch.setattr('backend.application.workflows.auth_qr.login_cookie_changed_since',
        lambda platform, profile, since: issued_at.append(since) or True)
    result = check_round(tmp_path, incident_id, now='2026-10-21T01:00:05Z')
    assert result['next_run_at'] == '2026-10-21T01:00:10Z'
    assert issued_at == [parse_utc(NOW)]
    def verify(base_dir, incident_id):
        with AuthRecoveryRepository(base_dir).connect() as conn:
            conn.execute("update auth_recovery_incidents set status='resolved',recovered_task_count=2 where incident_id=?", (incident_id,))
        return {'ok': True}
    monkeypatch.setattr(handlers, 'verify_auth_recovery', verify)
    task = tasks.get(f'qrcapture_{incident_id}_1_verify')
    assert handlers.handle_auth_qr_capture(task, tmp_path)['status'] == 'success'
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'resolved'
    assert not paths[-1].exists() and len(closed) == 1
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        recovered = conn.execute("select title,body from notification_outbox where kind='auth_recovered'").fetchone()
        assert recovered['title'] == '小红书 登录有效，已恢复采集'
        assert recovered['body'] == '登录验证通过，2 个采集任务已重新排队。'
    check_round(tmp_path, incident_id, now=NOW)
    assert len(closed) == 1


def test_browser_console_manual_resolved_stops_round(tmp_path, monkeypatch, qr_setup):
    incident, tasks, paths, closed = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)
    with AuthRecoveryRepository(tmp_path).connect() as conn:
        conn.execute("update auth_recovery_incidents set status='resolved' where incident_id=?", (incident_id,))
    check_round(tmp_path, incident_id, now=NOW)
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'resolved'
    assert not paths[-1].exists() and len(closed) == 1


@pytest.mark.parametrize('code', ['qr_element_not_found', 'qr_opencli_click_failed'])
def test_capture_failure_falls_back_without_retry(tmp_path, monkeypatch, qr_setup, code):
    incident, tasks, paths, closed = qr_setup
    start_qr_round(tmp_path, incident['incident_id'], now=NOW)
    monkeypatch.setattr(handlers, 'capture_qr', lambda *a: (_ for _ in ()).throw(QrCaptureError(code)))
    assert _capture(tmp_path, tasks, incident['incident_id'], 1, 1, monkeypatch, NOW)['status'] == 'success'
    assert AuthQrRepository(tmp_path).get(incident['incident_id'])['status'] == 'idle'
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        row = conn.execute('select kind,body from notification_outbox').fetchone()
        assert row['kind'] == 'auth_recovery_text' and '本轮扫码结束：' in row['body'] and f'（{code}）' in row['body']
    assert paths == [] and len(closed) == 1


def test_quiet_deferred_manual_override_and_daily_cap(tmp_path, qr_setup):
    incident, tasks, _, _ = qr_setup
    incident_id = incident['incident_id']
    quiet = '2026-10-21T16:00:00Z'
    assert start_qr_round(tmp_path, incident_id, now=quiet)['status'] == 'deferred'
    assert start_qr_round(tmp_path, incident_id, now=quiet, manual=True)['status'] == 'capturing'
    with AuthQrRepository(tmp_path).connect() as conn:
        conn.execute("update auth_qr_rounds set status='idle',rounds_today=4,day='2026-10-21',next_round_at=? where incident_id=?", (NOW, incident_id))
    assert start_qr_round(tmp_path, incident_id, now=NOW)['round_no'] == 1
    assert start_qr_round(tmp_path, incident_id, now=quiet, manual=True)['round_no'] == 2
    with AuthQrRepository(tmp_path).connect() as conn:
        conn.execute("update auth_qr_rounds set status='idle' where incident_id=?", (incident_id,))
    next_day = format_utc_seconds_z(parse_utc(quiet) + timedelta(days=1))
    assert start_qr_round(tmp_path, incident_id, now=next_day, manual=True)['round_no'] == 3


def test_unavailable_browser_falls_back_even_if_tab_close_fails(tmp_path, monkeypatch, qr_setup):
    incident, tasks, _, _ = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    monkeypatch.setattr('backend.application.workflows.auth_qr.close_qr_tab', lambda *a: (_ for _ in ()).throw(QrCaptureError('qr_tab_close_failed')))
    task = tasks.get(f'qrcapture_{incident_id}_1_1')
    monkeypatch.setattr(handlers, 'current_iso', lambda: NOW)
    assert handlers.qr_browser_unavailable(task, tmp_path)['status'] == 'success'
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'closing'
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        row = conn.execute("select status,body from notification_outbox where kind='auth_recovery_text'").fetchone()
        assert row['status'] == 'pending' and 'browser_session_not_ready' in row['body']
    monkeypatch.setattr('backend.application.workflows.auth_qr.close_qr_tab', lambda *a: None)
    check_round(tmp_path, incident_id, now='2026-10-21T01:00:05Z')
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'idle'
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        assert conn.execute("select status from notification_outbox where kind='auth_recovery_text'").fetchone()[0] == 'pending'


def test_restart_after_enqueue_preserves_existing_code(tmp_path, monkeypatch, qr_setup):
    incident, tasks, paths, _ = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    original = AuthQrRepository.captured
    monkeypatch.setattr(AuthQrRepository, 'captured', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('process interrupted')))
    with pytest.raises(RuntimeError, match='process interrupted'):
        _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)
    assert len(paths) == 1
    old = NotificationOutboxRepository(tmp_path).by_dedupe(f'{incident_id}:1:1', channel='feishu')
    monkeypatch.setattr(AuthQrRepository, 'captured', original)
    _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, '2026-10-21T01:00:05Z')
    assert len(paths) == 1
    row = AuthQrRepository(tmp_path).get(incident_id)
    assert row['codes_sent'] == 1 and row['code_expires_at'] == old['expires_at']


def test_failed_verification_reports_result(tmp_path, monkeypatch, qr_setup):
    incident, tasks, _, _ = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)
    monkeypatch.setattr('backend.application.workflows.auth_qr.login_cookie_changed_since', lambda *a: True)
    check_round(tmp_path, incident_id, now=NOW)
    monkeypatch.setattr(handlers, 'verify_auth_recovery',
        lambda *a: {'ok': False, 'verification': {'code': 'platform_session_not_ready'}})
    assert handlers.handle_auth_qr_capture(tasks.get(f'qrcapture_{incident_id}_1_verify'), tmp_path)['status'] == 'success'
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'idle'
    reply = NotificationOutboxRepository(tmp_path).by_dedupe(f'qr-end:{incident_id}:1', channel='feishu')
    assert reply['title'] == '小红书 扫码后未登录成功'
    assert reply['body'] == '检测到扫码，但登录验证未通过（platform_session_not_ready）。回复「1」重新生成。'


@pytest.mark.parametrize('recover_after_capture', [False, True])
def test_already_logged_in_resolves_incident_and_requeues_fetch(tmp_path, monkeypatch, qr_setup, recover_after_capture):
    from backend.application.workflows.auth_recovery import verify_auth_recovery
    from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus
    from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
    incident, tasks, paths, closed = qr_setup
    incident_id = incident['incident_id']
    RuntimeCircuitRepository(tmp_path).open_manual('platform', 'xiaohongshu', occurred_at=NOW,
        reason_code='platform_not_logged_in', user_action='login', task_id='fetch-qr')
    with sqlite3.connect(tasks.db_path) as conn:
        conn.execute("update tasks set status='blocked', last_failure_class='platform_not_logged_in' where task_id='fetch-qr'")
    start_qr_round(tmp_path, incident_id, now=NOW)
    statuses = iter([AccountStatus.NEEDS_LOGIN, AccountStatus.VERIFIED] if recover_after_capture else [AccountStatus.VERIFIED])
    monkeypatch.setattr(handlers, 'verify_auth_recovery', verify_auth_recovery)
    monkeypatch.setattr('backend.application.workflows.auth_recovery.probe_platform', lambda _: AccountState(status=next(statuses)))
    captured = []

    def capture(*args):
        captured.append(args)
        raise QrCaptureError('qr_element_not_found')

    monkeypatch.setattr(handlers, 'capture_qr', capture)
    assert _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW) == {'status': 'success'}
    assert len(captured) == int(recover_after_capture)
    assert AuthRecoveryRepository(tmp_path).get(incident_id)['status'] == 'resolved'
    assert tasks.get('fetch-qr').status.value == 'pending'
    assert RuntimeCircuitRepository(tmp_path).get('platform', 'xiaohongshu')['state'] == 'closed'
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'resolved'
    assert paths == [] and len(closed) == 1
    with NotificationOutboxRepository(tmp_path).connect_existing() as conn:
        rows = conn.execute('select kind,title from notification_outbox').fetchall()
        assert [(row['kind'], row['title']) for row in rows] == [('auth_recovered', '小红书 登录有效，已恢复采集')]


@pytest.mark.parametrize('status,error', [
    ('failed', 'Browser connection dropped after the exec command was dispatched'),
    ('verified', None),
    ('needs_login', 'Browser profile infoos-fetch is not connected'),
])
def test_unconfirmed_preflight_stops_without_capturing(tmp_path, monkeypatch, qr_setup, status, error):
    from backend.application.workflows.auth_recovery import verify_auth_recovery
    from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus
    incident, tasks, paths, closed = qr_setup
    incident_id = incident['incident_id']
    start_qr_round(tmp_path, incident_id, now=NOW)
    monkeypatch.setattr(handlers, 'verify_auth_recovery', verify_auth_recovery)
    monkeypatch.setattr('backend.application.workflows.auth_recovery.probe_platform',
        lambda _: AccountState(status=AccountStatus(status), error=error))
    assert _capture(tmp_path, tasks, incident_id, 1, 1, monkeypatch, NOW)['status'] == 'success'
    assert paths == [] and len(closed) == 1
    assert AuthQrRepository(tmp_path).get(incident_id)['status'] == 'idle'
    assert AuthRecoveryRepository(tmp_path).get(incident_id)['status'] == 'awaiting_user'
