"""Claim one notification, deliver outside SQL transactions, persist its result."""
from datetime import timedelta
from pathlib import Path

from backend.application.tasks.execution_clock import current_iso
from backend.application.tasks.types import Task
from backend.application.usecases.notifications import delete_image
from backend.domain.notifications import NotificationKind, delivery_time
from backend.domain.timestamps import parse_utc, format_utc_seconds_z
from backend.infrastructure.notifications.channels.email import EmailChannel
from backend.infrastructure.notifications.channels.feishu import FeishuChannel
from backend.infrastructure.notifications.config import load_config, readiness
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository


def handle_notification_deliver(task: Task, base_dir: Path, *, now: str | None = None) -> dict:
    return deliver_notification(base_dir, task.target_id, now=now or current_iso())


def deliver_notification(base_dir: Path | str, notification_id: str, *, now: str) -> dict:
    repo = NotificationOutboxRepository(base_dir)
    existing = repo.get(notification_id)
    if existing is None:
        return {'status': 'skipped', 'artifacts': {'reason': 'notification_missing'}}
    if existing['status'] not in {'pending', 'sending'}:
        _cleanup(repo, existing)
        return {'status': 'success', 'artifacts': {'notification_status': existing['status']}}
    clock = parse_utc(now)
    row = repo.claim(notification_id, now=now, lease_until=format_utc_seconds_z(clock + timedelta(seconds=60)))
    if row is None:
        due = max(existing['next_attempt_at'], existing['lease_until'] or now)
        return {'status': 'pending', 'next_run_at': due}
    config = load_config()
    due = delivery_time(clock, config['quiet_hours'], NotificationKind(row['kind']), manual=bool(row['quiet_allowed']))
    cancelled = False
    if row['related_kind'] == 'auth_incident' and row['kind'] in {'auth_recovery_qr', 'auth_recovery_text'}:
        incident = AuthRecoveryRepository(base_dir).get(row['related_id'])
        cancelled = incident is None or incident['status'] == 'resolved'
    status, error, provider_id = 'expired', None, None
    next_attempt = now
    if not cancelled and parse_utc(row['expires_at']) > clock:
        if due > clock:
            status, next_attempt = 'pending', format_utc_seconds_z(min(due, parse_utc(row['expires_at'])))
        elif not readiness(config)[row['channel']]:
            status, error = 'skipped', 'channel_not_ready'
        else:
            channel = FeishuChannel(config['channels']['feishu']) if row['channel'] == 'feishu' else EmailChannel(config)
            result = channel.send(title=row['title'], body=row['body'], image_path=row['image_path'], message_id=notification_id)
            status, error, provider_id = result.status, result.error_code, result.provider_message_id
            if status == 'retryable':
                status = 'pending'
                next_attempt = format_utc_seconds_z(min(parse_utc(row['expires_at']),
                    clock + timedelta(seconds=min(3600, 5 * 2 ** min(row['attempts'] - 1, 10)))))
    changed = repo.finish(row, status=status, now=now, next_attempt_at=next_attempt,
                          error_code=error, provider_message_id=provider_id)
    if changed and status != 'pending':
        _cleanup(repo, row)
    if status == 'pending':
        return {'status': 'pending', 'next_run_at': next_attempt, 'artifacts': {'notification_status': status}}
    return {'status': 'success', 'artifacts': {'notification_status': status, 'error_code': error}}


def _cleanup(repo: NotificationOutboxRepository, row: dict) -> None:
    if row['image_path'] is not None and not repo.image_in_use(row['image_path']):
        delete_image(row['image_path'])
