import json
import stat

import pytest

from backend.api.routes.reader_auth import is_reader_request
from backend.infrastructure.notifications import connect_session as sessions, rules
from backend.infrastructure.notifications.config import load_config
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from tests.infoos import lark_cli_fakes
from tests.infoos.lark_cli_fakes import eventually

fake_lark = lark_cli_fakes.fake_lark
from tests.infoos.web.test_notification_settings import client_for

ROOT = '/api/settings/notifications/feishu/connect'


def status(client, *, step=None, state='waiting_scan', qr=False):
    value = client.get(ROOT).json()
    if value['status'] != state or (step is not None and value['step'] != step):
        return None
    if qr and not value['qr_version']:
        return None
    assert set(value) == {'step', 'status', 'expires_at', 'error_code', 'qr_version'}
    assert not any(secret in json.dumps(value) for secret in ('https://', 'device_code', 'synthetic-device', 'appId'))
    return value


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    return client_for(tmp_path)


def test_two_scans_single_session_and_private_qr(client, fake_lark, tmp_path):
    assert client.get(ROOT).json()['status'] == 'idle'
    assert client.get(ROOT + '/qr.png').status_code == 404
    assert client.post(ROOT).json()['status'] == 'waiting_scan'
    first = eventually(lambda: status(client, step='create_app', qr=True))
    assert client.post(ROOT).json() == first
    assert len([c for c in fake_lark.calls() if c['args'][:2] == ['config', 'init']]) == 1
    image = client.get(ROOT + '/qr.png')
    assert image.status_code == 200 and image.content.startswith(b'\x89PNG')
    assert image.headers['cache-control'] == 'no-store'
    assert stat.S_IMODE((sessions.setup_dir() / 'connect-qr.png').stat().st_mode) == 0o600
    fake_lark.scan('app_scan')
    second = eventually(lambda: status(client, step='authorize_user', qr=True))
    assert second['qr_version'] != first['qr_version']
    fake_lark.scan('user_scan')
    eventually(lambda: status(client, step='done', state='completed'))
    config = load_config()
    assert config['channels']['feishu'] == {'enabled': True, 'receive_id': 'ou_synthetic'}
    assert config['enabled'] is True
    rows = NotificationOutboxRepository(tmp_path).latest_results()
    assert len(rows) == 1 and rows[0]['status'] == 'pending'
    assert client.get(ROOT + '/qr.png').status_code == 404
    session_path = sessions.setup_dir() / 'session.json'
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    assert 'synthetic-device' not in session_path.read_text() and 'https://' not in session_path.read_text()
    login = next(c for c in fake_lark.calls() if '--no-wait' in c['args'])
    assert login['args'] == ['auth', 'login', '--no-wait', '--json', '--scope', 'offline_access']
    for call in fake_lark.calls():
        assert 'UNRELATED_SECRET' not in call['env']
        assert 'INFOOS_LARK_CLI' not in call['env']


def test_configured_and_logged_in_direct_done(client, fake_lark):
    fake_lark.logged_in()
    client.post(ROOT)
    eventually(lambda: status(client, step='done', state='completed'))
    assert [c['args'] for c in fake_lark.calls()] == [['auth', 'status', '--json']]


def test_configured_without_user_skips_create_app(client, fake_lark):
    fake_lark.app()
    client.post(ROOT)
    eventually(lambda: status(client, step='authorize_user', qr=True))
    assert all(c['args'][:2] != ['config', 'init'] for c in fake_lark.calls())
    fake_lark.scan('user_scan')
    eventually(lambda: status(client, state='completed'))


def test_failure_and_restart_after_failure(client, fake_lark):
    fake_lark.control(init_error=True)
    client.post(ROOT)
    result = eventually(lambda: status(client, state='failed'))
    assert result['error_code'] == 'lark_network'
    fake_lark.control()
    client.post(ROOT)
    eventually(lambda: status(client, step='create_app', qr=True))


def test_cancel_ends_process_group_and_removes_qr(client, fake_lark):
    client.post(ROOT)
    eventually(lambda: status(client, qr=True))
    pid = sessions.read()['pid']
    assert sessions.alive(pid)
    assert client.delete(ROOT).json()['status'] == 'idle'
    assert not sessions.alive(pid)
    assert client.get(ROOT + '/qr.png').status_code == 404
    assert not (sessions.setup_dir() / 'connect-qr-next.png').exists()
    assert client.get(ROOT).json()['status'] == 'idle'


def test_new_web_instance_recovers_detached_session(client, fake_lark, tmp_path):
    fake_lark.app()
    client.post(ROOT)
    first = eventually(lambda: status(client, step='authorize_user', qr=True))
    # Drop web-process bookkeeping: recovery must use persisted PID/OS state.
    child = sessions._CHILDREN.pop(sessions.read()['pid'])
    try:
        restarted = client_for(tmp_path)
        assert restarted.get(ROOT).json() == first
        fake_lark.scan('user_scan')
        eventually(lambda: status(restarted, state='completed'))
        assert load_config()['channels']['feishu']['receive_id'] == 'ou_synthetic'
    finally:
        child.wait(timeout=8)


def test_web_marks_dead_worker_and_expired_sessions_failed(client, fake_lark):
    client.post(ROOT)
    eventually(lambda: status(client, qr=True))
    sessions.stop(sessions.read()['pid'])
    assert client.get(ROOT).json()['error_code'] == 'lark_setup_interrupted'
    client.post(ROOT)
    eventually(lambda: status(client, qr=True))
    with sessions.locked():
        value = sessions.read()
        value['expires_at'] = '2000-01-01T00:00:00Z'
        sessions.save(value)
    assert client.get(ROOT).json()['error_code'] == 'lark_setup_expired'
    assert client.get(ROOT + '/qr.png').status_code == 404


def test_lan_only_same_origin_and_public_response_shape(client, fake_lark):
    for method in ('POST', 'DELETE'):
        assert client.request(method, ROOT, headers={'origin': 'https://foreign.test'}).status_code == 403
    for path, method in [(ROOT, 'GET'), (ROOT, 'POST'), (ROOT, 'DELETE'), (ROOT + '/qr.png', 'GET')]:
        assert not is_reader_request(path, method)
    assert client.post(ROOT.replace('/connect', '/resolve-recipient'), json={'mobile': '123456789'}).status_code == 404
    assert set(client.get(ROOT).json()) == set(sessions.PUBLIC_FIELDS)


def test_default_base_dir_keeps_runtime_database_location(client, fake_lark, tmp_path, monkeypatch):
    monkeypatch.setenv('INFOOS_DATA_HOME', str(tmp_path / 'data'))
    fake_lark.logged_in()
    runtime_client = client_for('.')
    runtime_client.post(ROOT)
    eventually(lambda: status(runtime_client, state='completed'))
    assert NotificationOutboxRepository(tmp_path).latest_results()[0]['status'] == 'pending'
