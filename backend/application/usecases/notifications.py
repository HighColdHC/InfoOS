"""The single entry point for outbound notification intent."""
from datetime import timedelta
from pathlib import Path

from backend.domain.notifications import NotificationKind, delivery_time, route_channels
from backend.domain.timestamps import utc_now_seconds_z, parse_utc, format_utc_seconds_z
from backend.infrastructure.notifications.config import load_config, readiness
from backend.infrastructure.runtime.environment import state_home
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository


def delete_image(path: str | None) -> None:
    if path is not None:
        target = Path(path).resolve()
        # Outbox image paths are credential files, never arbitrary filesystem paths.
        if not target.is_relative_to((state_home() / 'auth-qr').resolve()):
            raise ValueError('invalid_qr_image_path')
        target.unlink(missing_ok=True)


def enqueue_notification(base_dir: Path | str, *, kind: NotificationKind, title: str, body: str,
                         dedupe_key: str, image_path: str | None = None, expires_at: str | None = None,
                         related: tuple[str, str] | None = None, manual: bool = False,
                         now: str | None = None) -> list[str]:
    timestamp = now or utc_now_seconds_z()
    config = load_config()
    channels = route_channels(kind, readiness(config))
    if image_path is not None:
        target = Path(image_path).resolve()
        if not target.is_relative_to((state_home() / 'auth-qr').resolve()):
            raise ValueError('invalid_qr_image_path')
    due = format_utc_seconds_z(delivery_time(parse_utc(timestamp), config['quiet_hours'], kind, manual=manual))
    expiry = expires_at if expires_at is not None else format_utc_seconds_z(parse_utc(timestamp) + timedelta(days=2))
    repo = NotificationOutboxRepository(base_dir)
    ids = [repo.enqueue(kind=kind.value, channel=channel, dedupe_key=dedupe_key, title=title, body=body,
        image_path=image_path if channel == 'feishu' else None, related=related, now=timestamp,
        next_attempt_at=due, expires_at=expiry, quiet_allowed=manual and kind == NotificationKind.AUTH_RECOVERY_QR)
        for channel in channels]
    ids = [notification_id for notification_id in ids if notification_id is not None]
    if image_path is not None and not repo.image_in_use(image_path):
        delete_image(image_path)
    return ids
