import json
import stat

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.settings_notifications import router
from backend.api.security import SameOriginManagementMiddleware
from backend.api.routes.reader_auth import is_reader_request
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.config import load_config, save_config, readiness
from tests.infoos import lark_cli_fakes

fake_lark = lark_cli_fakes.fake_lark


def client_for(base_dir):
    app = FastAPI()
    app.state.base_dir = base_dir
    app.add_middleware(SameOriginManagementMiddleware)
    app.include_router(router)
    return TestClient(app)


def test_settings_migration_and_same_origin(tmp_path, monkeypatch, fake_lark):
    path = tmp_path / 'notifications.json'
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', path)
    path.write_text(json.dumps({'channels': {'feishu': {'enabled': True, 'app_id': 'old-id',
        'app_secret': 'old-test-secret', 'domain': 'lark', 'receive_id': 'ou_fixture'}}}))
    client = client_for(tmp_path)
    response = client.get('/api/settings/notifications')
    assert response.json()['config']['channels']['feishu'] == {'enabled': True, 'receive_id': 'ou_fixture'}
    assert 'old-test-secret' not in response.text
    response = client.put('/api/settings/notifications', json={'enabled': True})
    assert response.status_code == 200
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert 'old-test-secret' not in path.read_text()
    assert readiness(load_config())['feishu'] is False
    fake_lark.app()
    assert readiness(load_config())['feishu'] is True
    invalid = client.put('/api/settings/notifications', json={'channels': {'feishu': {'app_secret': 'never-echo'}}})
    assert invalid.status_code == 422 and 'never-echo' not in invalid.text
    assert client.put('/api/settings/notifications', json={'quiet_hours': {'start': '25:00'}}).status_code == 422
    assert client.put('/api/settings/notifications', json={'auth_qr': {'codes_per_round': 0}}).status_code == 422
    assert client.put('/api/settings/notifications', json={}, headers={'origin': 'https://foreign.test'}).status_code == 403
    assert not is_reader_request('/api/settings/notifications', 'GET')
    assert not is_reader_request('/api/auth-recoveries/test/qr-round', 'POST')


def test_old_file_defaults_and_email_password_redaction(tmp_path, monkeypatch):
    path = tmp_path / 'notifications.json'
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', path)
    path.write_text(json.dumps({'enabled': True, 'email': {'password': 'fixture-password'}}))
    config = load_config()
    assert config['auth_qr']['codes_per_round'] == 3
    save_config(config)
    response = client_for(tmp_path).get('/api/settings/notifications')
    assert response.status_code == 200 and 'fixture-password' not in response.text
    assert response.json()['config']['email']['password_configured']


def test_synchronous_channel_test_uses_outbox_and_fake_cli(tmp_path, monkeypatch, fake_lark):
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    config = load_config()
    config['enabled'] = True
    config['channels']['feishu'].update(enabled=True, receive_id='ou_fixture')
    fake_lark.app()
    save_config(config)
    response = client_for(tmp_path).post('/api/settings/notifications/test')
    assert response.json()['results'] == [{'channel': 'feishu', 'status': 'sent', 'error_code': None}]
    assert len(fake_lark.calls()) == 1
