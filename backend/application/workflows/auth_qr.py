"""Restartable QR rounds; passive checks never hold a browser resource slot."""
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.application.usecases.notifications import enqueue_notification, delete_image
from backend.domain.feishu_commands import qr_menu_number
from backend.domain.notifications import NotificationKind, delivery_time
from backend.domain.sources.registry import platform_definition
from backend.domain.timestamps import parse_utc, format_utc_seconds_z, utc_now_seconds_z
from backend.infrastructure.browser.cookie_status import login_cookie_changed_since
from backend.infrastructure.browser.qr_capture import close_qr_tab, image_path, QrCaptureError
from backend.infrastructure.notifications.config import load_config, readiness
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.infrastructure.storage.auth_qr import AuthQrRepository
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository


class QrRoundUnavailable(ValueError):
    pass


def start_qr_round(base_dir: Path | str, incident_id: str, *, manual: bool = False, now: str | None = None) -> dict:
    timestamp = now or utc_now_seconds_z()
    incident = AuthRecoveryRepository(base_dir).get(incident_id)
    if incident is None:
        raise QrRoundUnavailable('auth_recovery_not_found')
    if incident['status'] == 'resolved':
        raise QrRoundUnavailable('recovery_not_actionable')
    definition = platform_definition(incident['platform'])
    if definition is None or definition.qr_login is None:
        raise QrRoundUnavailable('qr_login_unsupported')
    config = load_config()
    if not readiness(config)['feishu']:
        raise QrRoundUnavailable('feishu_not_ready')
    clock = parse_utc(timestamp)
    due = delivery_time(clock, config['quiet_hours'], NotificationKind.AUTH_RECOVERY_QR, manual=manual)
    if due > clock:
        _wake(base_dir, incident_id, due=format_utc_seconds_z(due), now=timestamp)
        return {'status': 'deferred', 'next_round_at': format_utc_seconds_z(due)}
    row = AuthQrRepository(base_dir).start(incident, now=timestamp, manual=manual,
        day=clock.astimezone(ZoneInfo(config['quiet_hours']['timezone'])).date().isoformat(),
        day_limit=config['auth_qr']['max_rounds_per_day'])
    if row['status'] == 'idle':
        tomorrow = clock.astimezone(ZoneInfo(config['quiet_hours']['timezone'])).replace(hour=0, minute=0, second=0) + timedelta(days=1)
        retry = max(parse_utc(row['next_round_at']), tomorrow) if row['rounds_today'] >= config['auth_qr']['max_rounds_per_day'] else parse_utc(row['next_round_at'])
        due = delivery_time(retry, config['quiet_hours'], NotificationKind.AUTH_RECOVERY_QR)
        _wake(base_dir, incident_id, due=format_utc_seconds_z(due), now=timestamp)
    return row


def round_text(base_dir, incident: dict, row: dict, *, now: str, code: str) -> None:
    label = platform_definition(incident['platform']).label
    reason = {
        'qr_element_not_found': '未找到登录二维码',
        'browser_session_not_ready': '浏览器连接尚未就绪',
        'qr_runtime_unavailable': '登录验证或二维码运行环境暂不可用',
        'platform_session_not_ready': '登录探针暂时无法确认登录状态',
        'recovery_tasks_not_ready': '采集任务尚未就绪，暂时无法恢复',
        'qr_round_expired': '本轮二维码已过期',
        'qr_capture_interrupted': '二维码捕获已中断',
        'feishu_not_ready': '飞书通知暂不可用',
        'quiet_hours': '已进入免打扰时段',
        'qr_capture_failed': '二维码捕获失败',
    }.get(code, '二维码操作暂时失败')
    enqueue_notification(base_dir, kind=NotificationKind.AUTH_RECOVERY_TEXT,
        title=f'{label} 登录等待恢复',
        body=f"本轮扫码结束：{reason}（{code}）。约 {load_config()['auth_qr']['round_interval_hours']} 小时后重发。回复「{qr_menu_number(incident['platform'])}」可立即重新生成。",
        related=('auth_incident', incident['incident_id']), dedupe_key=f"qr-end:{incident['incident_id']}:{row['round_no']}", now=now)


def verification_failed_text(base_dir, incident: dict, row: dict, *, now: str, code: str) -> None:
    label = platform_definition(incident['platform']).label
    enqueue_notification(base_dir, kind=NotificationKind.AUTH_RECOVERY_TEXT,
        title=f'{label} 扫码后未登录成功',
        body=f"检测到扫码，但登录验证未通过（{code}）。回复「{qr_menu_number(incident['platform'])}」重新生成。",
        related=('auth_incident', incident['incident_id']),
        dedupe_key=f"qr-end:{incident['incident_id']}:{row['round_no']}", now=now)


def stop_round(base_dir, incident: dict, row: dict, *, now: str, code: str | None = None) -> None:
    repo = AuthQrRepository(base_dir)
    clock, config = parse_utc(now), load_config()
    resolved = incident['status'] == 'resolved'
    due = parse_utc(row['next_round_at']) if row['status'] == 'closing' else delivery_time(
        clock + timedelta(hours=config['auth_qr']['round_interval_hours']), config['quiet_hours'], NotificationKind.AUTH_RECOVERY_QR)
    repo.close(incident['incident_id'], now=now, next_round_at=format_utc_seconds_z(due))
    outbox = NotificationOutboxRepository(base_dir)
    for path in outbox.cancel_incident(incident['incident_id'], now=now, include_text=resolved):
        delete_image(path)
    for n in range(1, row['codes_sent'] + 2):
        delete_image(str(image_path(incident['incident_id'], row['round_no'], n)))
    if resolved:
        enqueue_notification(base_dir, kind=NotificationKind.AUTH_RECOVERED,
            title=f"{platform_definition(incident['platform']).label} 登录有效，已恢复采集",
            body=f"登录验证通过，{incident['recovered_task_count']} 个采集任务已重新排队。",
            related=('auth_incident', incident['incident_id']), dedupe_key=f"auth-recovered:{incident['incident_id']}", now=now)
    else:
        round_text(base_dir, incident, row, now=now, code=code or 'qr_round_expired')
    # A failed close keeps 'closing' durable; the next maintenance check retries.
    try:
        close_qr_tab(row['tab_site'])
    except (QrCaptureError, OSError, TimeoutError):
        _wake(base_dir, incident['incident_id'], due=format_utc_seconds_z(clock + timedelta(seconds=5)), now=now)
        return
    repo.closed(incident['incident_id'], now=now, resolved=resolved)
    if not resolved:
        _wake(base_dir, incident['incident_id'], due=format_utc_seconds_z(due), now=now)


def check_round(base_dir, incident_id: str, *, now: str) -> dict:
    repo, incidents = AuthQrRepository(base_dir), AuthRecoveryRepository(base_dir)
    row, incident = repo.get(incident_id), incidents.get(incident_id)
    if incident is None or (row is not None and row['status'] == 'resolved'):
        return {'status': 'success'}
    clock, config = parse_utc(now), load_config()
    next_check = format_utc_seconds_z(clock + timedelta(seconds=5))
    if row is None or (row['status'] == 'idle' and incident['status'] != 'resolved'):
        if incident['status'] != 'resolved':
            try:
                start_qr_round(base_dir, incident_id, now=now)
            except QrRoundUnavailable:
                pass
        return {'status': 'success'}
    if incident['status'] == 'resolved' or row['status'] == 'closing':
        try:
            stop_round(base_dir, incident, row, now=now)
        except (QrCaptureError, OSError, TimeoutError):
            return {'status': 'pending', 'next_run_at': next_check}
        if repo.get(incident_id)['status'] == 'closing':
            return {'status': 'pending', 'next_run_at': next_check}
        return {'status': 'success'}
    if row['status'] == 'idle':
        return {'status': 'success'}
    if not readiness(config)['feishu']:
        stop_round(base_dir, incident, row, now=now, code='feishu_not_ready')
        return {'status': 'success'}
    if row['status'] == 'waiting':
        capability = platform_definition(incident['platform']).qr_login
        issued_at = parse_utc(row['code_expires_at']) - timedelta(seconds=capability.qr_ttl_seconds)
        scanned = login_cookie_changed_since(incident['platform'], chrome_profile_dir(), issued_at)
        if scanned:
            repo.next_capture(row, platform=incident['platform'], now=now, verify=True)
        elif row['code_expires_at'] <= now:
            due = delivery_time(clock, config['quiet_hours'], NotificationKind.AUTH_RECOVERY_QR, manual=bool(row['manual']))
            if due > clock:
                stop_round(base_dir, incident, row, now=now, code='quiet_hours')
                return {'status': 'success'}
            delete_image(str(image_path(incident_id, row['round_no'], row['codes_sent'])))
            if row['codes_sent'] < config['auth_qr']['codes_per_round']:
                repo.next_capture(row, platform=incident['platform'], now=now)
            else:
                stop_round(base_dir, incident, row, now=now)
                return {'status': 'success'}
    elif (clock - parse_utc(row['updated_at'])).total_seconds() > 120:
        stop_round(base_dir, incident, row, now=now, code='qr_capture_interrupted')
        return {'status': 'success'}
    return {'status': 'pending', 'next_run_at': next_check}


def drain_qr_rounds(base_dir, *, now: str) -> None:
    repo = AuthQrRepository(base_dir)
    for incident_id in repo.actionable_ids():
        row = repo.get(incident_id)
        if row is not None and row['status'] not in {'idle', 'resolved'}:
            check_round(base_dir, incident_id, now=now)
        else:
            try:
                start_qr_round(base_dir, incident_id, now=now)
            except QrRoundUnavailable:
                continue


def _wake(base_dir, incident_id: str, *, due: str, now: str) -> None:
    from backend.domain.tasks import Task, TaskType
    from backend.application.tasks.repository import TaskRepository
    TaskRepository.for_base_dir(base_dir).enqueue(Task(task_id=f"qrwake_{incident_id}_{due}", task_type=TaskType.AUTH_QR_CHECK,
        target_kind='auth_incident', target_id=incident_id, next_run_at=due))


def finish_resolved_incident(base_dir, incident: dict, *, now: str) -> None:
    row = AuthQrRepository(base_dir).get(incident['incident_id'])
    if row is not None and row['status'] != 'resolved':
        # Cleanup belongs to the durable maintenance worker, including retrying a failed close.
        _wake(base_dir, incident['incident_id'], due=now, now=now)
    elif row is None:
        for path in NotificationOutboxRepository(base_dir).cancel_incident(incident['incident_id'], now=now):
            delete_image(path)
        enqueue_notification(base_dir, kind=NotificationKind.AUTH_RECOVERED,
            title=f"{platform_definition(incident['platform']).label} 登录有效，已恢复采集",
            body=f"登录验证通过，{incident['recovered_task_count']} 个采集任务已重新排队。",
            related=('auth_incident', incident['incident_id']), dedupe_key=f"auth-recovered:{incident['incident_id']}", now=now)
