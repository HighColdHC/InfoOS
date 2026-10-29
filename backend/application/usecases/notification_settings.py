"""Manage notification config without disclosing stored secrets."""
from uuid import uuid4

from backend.application.tasks.handlers.notification_deliver import deliver_notification
from backend.application.usecases.notifications import enqueue_notification
from backend.domain.notifications import NotificationKind
from backend.domain.timestamps import utc_now_seconds_z
from backend.infrastructure.notifications.config import load_config, save_config, merge, public_config, readiness
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository


def get_settings(base_dir) -> dict:
    config = load_config()
    return {'config': public_config(config), 'readiness': readiness(config),
            'latest_results': NotificationOutboxRepository(base_dir).latest_results()}


def update_settings(base_dir, patch: dict) -> dict:
    config = load_config()
    email = patch.get('email', {})
    if not email.get('password'):
        email.pop('password', None)
    merge(config, patch)
    save_config(config)
    return get_settings(base_dir)


def send_test(base_dir) -> dict:
    ids = enqueue_notification(base_dir, kind=NotificationKind.CHANNEL_TEST, title='InfoOS 通知测试',
        body='通知渠道已连接。', dedupe_key=f'channel-test:{uuid4().hex}')
    results = []
    for notification_id in ids:
        deliver_notification(base_dir, notification_id, now=utc_now_seconds_z())
        row = NotificationOutboxRepository(base_dir).get(notification_id)
        results.append({'channel': row['channel'], 'status': row['status'], 'error_code': row['last_error_code']})
    return {'results': results, 'readiness': readiness(load_config())}
