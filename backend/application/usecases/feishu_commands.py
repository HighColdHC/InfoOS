"""Accept the bound user's three private commands through the durable QR workflow."""
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.application.usecases.notifications import delete_image
from backend.application.workflows.auth_qr import start_qr_round
from backend.domain.feishu_commands import QR_PLATFORMS, menu_text, parse_command
from backend.domain.sources.registry import platform_definition
from backend.domain.timestamps import format_utc_seconds_z, parse_utc
from backend.infrastructure.browser.qr_capture import image_path
from backend.infrastructure.notifications.config import load_config, readiness
from backend.infrastructure.storage.auth_qr import AuthQrRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository


def handle_inbound(base_dir: Path | str, event: dict, *, now: str) -> None:
    config = load_config()
    if (event.get('chat_type') != 'p2p' or event.get('message_type') != 'text'
            or event.get('sender_id') != config['channels']['feishu']['receive_id']
            or not readiness(config)['feishu']):
        return
    message_id, text = event.get('message_id'), event.get('content')
    if not isinstance(message_id, str) or not message_id or not isinstance(text, str):
        return
    outbox = NotificationOutboxRepository(base_dir)
    receipt = outbox.claim_command(message_id, now=now,
        expires_at=format_utc_seconds_z(parse_utc(now) + timedelta(days=2)))
    if receipt is None:
        return
    command = parse_command(text)
    if command.kind == 'qr':
        body = _request_qr(base_dir, command.platform, request_id=receipt, now=now)
    elif command.kind == 'status':
        platforms = QR_PLATFORMS if command.platform is None else (command.platform,)
        body = '\n'.join(_status(base_dir, platform, now=now) for platform in platforms)
    else:
        body = menu_text()
    if body is not None:
        outbox.reply_command(receipt, title='InfoOS', body=body, now=now)


def _request_qr(base_dir, platform: str, *, request_id: str, now: str) -> str | None:
    repo = AuthQrRepository(base_dir)
    incident = repo.latest_awaiting(platform)
    if incident is None:
        return f'{platform_definition(platform).label} 当前没有掉线记录，不需要扫码。'
    incident_id = incident['incident_id']
    row = repo.get(incident_id)
    if row is None or row['status'] == 'idle':
        start_qr_round(base_dir, incident_id, manual=True, now=now)
    elif row['status'] == 'waiting':
        for path in NotificationOutboxRepository(base_dir).cancel_incident(incident_id, now=now, include_text=False):
            delete_image(path)
        delete_image(str(image_path(incident_id, row['round_no'], row['codes_sent'])))
        repo.next_capture(row, platform=platform, now=now, manual=True)
    elif row['status'] == 'capturing':
        return '正在生成，稍候。'
    elif row['status'] == 'closing':
        repo.restart_after_close(incident_id, request_id=request_id, now=now)
        return '正在重新生成，稍候。'
    return None


def _status(base_dir, platform: str, *, now: str) -> str:
    repo = AuthQrRepository(base_dir)
    incident = repo.latest_awaiting(platform)
    label = platform_definition(platform).label
    if incident is None:
        return f'{label}：没有掉线记录'
    beijing = ZoneInfo('Asia/Shanghai')
    outage = parse_utc(incident['created_at']).astimezone(beijing).strftime('%Y-%m-%d %H:%M')
    text = f'{label}：等待扫码登录（掉线于 {outage}）'
    row = repo.get(incident['incident_id'])
    if row is not None and row['status'] == 'waiting' and parse_utc(row['code_expires_at']) > parse_utc(now):
        expiry = parse_utc(row['code_expires_at']).astimezone(beijing).strftime('%H:%M')
        text += f'，当前二维码有效至 {expiry}'
    return text
