"""Exercise delivery with synthetic configuration and channel results."""
from datetime import datetime, timezone

import pytest

from backend.application.tasks.handlers.notification_deliver import deliver_notification
from backend.application.usecases.notifications import enqueue_notification
from backend.domain.notifications import NotificationKind as Kind, delivery_time
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.channels.feishu import FeishuChannel
from backend.infrastructure.notifications.config import load_config, save_config
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from backend.infrastructure.runtime.environment import state_home
from backend.infrastructure.notifications.channels import ChannelResult
from backend.infrastructure.notifications.lark_cli import prepare_directories

NOW = '2026-10-21T01:00:00Z'


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, 'DEFAULT_CONFIG_PATH', tmp_path / 'notifications.json')
    config = load_config()
    config['enabled'] = True
    config['channels']['feishu'].update(enabled=True, receive_id='ou_fixture')
    save_config(config)
    (prepare_directories() / 'config' / 'config.json').write_text('{}')
    return config

def test_outbox_dedupe_backoff_expiry_and_image_cleanup(configured, tmp_path, monkeypatch):
    calls = []
    def send(self, **kwargs):
        calls.append(kwargs)
        return ChannelResult('retryable', 'lark_network')
    monkeypatch.setattr(FeishuChannel, 'send', send)
    image = state_home() / 'auth-qr' / 'synthetic.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'synthetic-image')
    image.chmod(0o600)
    arguments = dict(kind=Kind.AUTH_RECOVERY_QR, title='t', body='b', dedupe_key='dedupe',
                     image_path=str(image), expires_at='2026-10-21T01:00:10Z', now=NOW)
    ids = enqueue_notification(tmp_path, **arguments)
    assert enqueue_notification(tmp_path, **arguments) == ids
    repo = NotificationOutboxRepository(tmp_path)
    result = deliver_notification(tmp_path, ids[0], now=NOW)
    assert result['next_run_at'] == '2026-10-21T01:00:05Z'
    assert image.exists()
    deliver_notification(tmp_path, ids[0], now='2026-10-21T01:00:10Z')
    assert repo.get(ids[0])['status'] == 'expired'
    assert not image.exists()
    assert len(calls) == 1
    with repo.connect_existing() as conn:
        assert conn.execute('select count(*) from tasks where task_type=?', ('notification_deliver',)).fetchone()[0] == 1


def test_sent_image_deleted_and_terminal_not_resent(configured, tmp_path, monkeypatch):
    calls = []
    def send(self, **kwargs):
        calls.append(kwargs)
        return ChannelResult('sent', provider_message_id='synthetic-message')
    monkeypatch.setattr(FeishuChannel, 'send', send)
    image = state_home() / 'auth-qr' / 'synthetic.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(b'synthetic-image')
    ids = enqueue_notification(tmp_path, kind=Kind.AUTH_RECOVERY_QR, title='t', body='b', dedupe_key='sent', image_path=str(image), now=NOW)
    assert deliver_notification(tmp_path, ids[0], now=NOW)['status'] == 'success'
    assert not image.exists()
    deliver_notification(tmp_path, ids[0], now=NOW)
    assert len(calls) == 1


def test_quiet_hours_and_manual_bypass():
    quiet = {'start': '23:00', 'end': '08:00', 'timezone': 'Asia/Shanghai'}
    now = datetime(2026, 10, 21, 16, tzinfo=timezone.utc)
    assert delivery_time(now, quiet, Kind.AUTH_RECOVERY_QR).isoformat() == '2026-10-22T00:00:00+00:00'
    assert delivery_time(now, quiet, Kind.AUTH_RECOVERY_QR, manual=True) == now
    assert delivery_time(now, quiet, Kind.CHANNEL_TEST) == now
    quiet.update(start='09:00', end='17:00')
    assert delivery_time(datetime(2026, 10, 21, 2, tzinfo=timezone.utc), quiet, Kind.AUTH_RECOVERED).hour == 9


def test_sending_lease_handover_fences_previous_worker(configured, tmp_path):
    ids = enqueue_notification(tmp_path, kind=Kind.CHANNEL_TEST, title='t', body='b', dedupe_key='lease', now=NOW)
    repo = NotificationOutboxRepository(tmp_path)
    first = repo.claim(ids[0], now=NOW, lease_until='2026-10-21T01:01:00Z')
    assert repo.claim(ids[0], now='2026-10-21T01:00:59Z', lease_until='2026-10-21T01:02:00Z') is None
    second = repo.claim(ids[0], now='2026-10-21T01:01:00Z', lease_until='2026-10-21T01:02:00Z')
    assert second['claim_token'] != first['claim_token']
    assert not repo.finish(first, status='sent', now=NOW, next_attempt_at=NOW)
    assert repo.finish(second, status='sent', now=NOW, next_attempt_at=NOW)


def test_disabled_channel_skips_then_configuration_requeues(configured, tmp_path, monkeypatch):
    ids = enqueue_notification(tmp_path, kind=Kind.CHANNEL_TEST, title='t', body='b', dedupe_key='skipped', now=NOW)
    configured['enabled'] = False
    save_config(configured)
    deliver_notification(tmp_path, ids[0], now=NOW)
    repo = NotificationOutboxRepository(tmp_path)
    assert repo.get(ids[0])['status'] == 'skipped'
    configured['enabled'] = True
    save_config(configured)
    assert enqueue_notification(tmp_path, kind=Kind.CHANNEL_TEST, title='t', body='b', dedupe_key='skipped', now=NOW) == ids
    assert repo.get(ids[0])['status'] == 'pending'
    calls = []
    def send(self, **kwargs):
        calls.append(kwargs)
        return ChannelResult('sent', provider_message_id='synthetic-message')
    monkeypatch.setattr(FeishuChannel, 'send', send)
    deliver_notification(tmp_path, ids[0], now=NOW)
    assert repo.get(ids[0])['status'] == 'sent'


def test_skipped_incident_requeues_then_worker_delivers(configured, tmp_path, monkeypatch):
    from backend.application.tasks.executor import run_due_tasks
    from backend.application.workflows.auth_recovery import drain_auth_recovery_notifications
    from backend.domain.tasks import TaskType
    from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
    from tests.infoos.test_auth_recovery import _incident
    incident = _incident(tmp_path)
    with AuthRecoveryRepository(tmp_path).connect() as conn:
        conn.execute("update auth_recovery_incidents set notification_status='skipped' where incident_id=?", (incident['incident_id'],))
    calls = []
    def send(self, **kwargs):
        calls.append(kwargs)
        return ChannelResult('sent', provider_message_id='synthetic-message')
    monkeypatch.setattr(FeishuChannel, 'send', send)
    assert drain_auth_recovery_notifications(tmp_path, now=NOW)['pending'] == 1
    result = run_due_tasks(tmp_path, now=NOW, task_types=[TaskType.NOTIFICATION_DELIVER])
    assert result['succeeded'] == 1
    assert AuthRecoveryRepository(tmp_path).get(incident['incident_id'])['notification_status'] == 'sent'
    assert len(calls) == 1
