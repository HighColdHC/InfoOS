"""Browser actions for capture/functional verification; maintenance-only polling."""
from datetime import timedelta
from pathlib import Path
from typing import cast

from backend.application.tasks.execution_clock import current_iso
from backend.application.usecases.notifications import enqueue_notification, delete_image
from backend.application.workflows.auth_qr import check_round, stop_round, start_qr_round, verification_failed_text
from backend.application.workflows.auth_recovery import verify_auth_recovery, AuthRecoveryRuntimeUnavailable, AuthRecoveryNotActionable, AuthRecoverySuperseded
from backend.domain.feishu_commands import qr_menu_number
from backend.domain.notifications import NotificationKind
from backend.domain.sources.registry import platform_definition
from backend.domain.timestamps import parse_utc, format_utc_seconds_z
from backend.infrastructure.browser.qr_capture import capture_qr, image_path, QrCaptureError
from backend.infrastructure.notifications.config import load_config
from backend.infrastructure.storage.auth_qr import AuthQrRepository
from backend.infrastructure.storage.notification_outbox import NotificationOutboxRepository
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository


def handle_auth_qr_check(task, base_dir: Path) -> dict:
    now = current_iso()
    if task.artifacts_summary.get('manual_restart'):
        row = AuthQrRepository(base_dir).get(task.target_id)
        if row['status'] == 'closing':
            result = check_round(base_dir, task.target_id, now=now)
            if result['status'] == 'pending':
                return result
        incident = AuthRecoveryRepository(base_dir).get(task.target_id)
        if incident['status'] == 'awaiting_user':
            start_qr_round(base_dir, task.target_id, manual=True, now=now)
        return {'status': 'success'}
    return check_round(base_dir, task.target_id, now=now)


def handle_auth_qr_capture(task, base_dir: Path) -> dict:
    repo = AuthQrRepository(base_dir)
    row, incident = repo.get(task.target_id), AuthRecoveryRepository(base_dir).get(task.target_id)
    if row is None or incident is None or row['round_no'] != task.artifacts_summary['round_no']:
        return {'status': 'skipped'}
    if incident['status'] == 'resolved':
        stop_round(base_dir, incident, row, now=current_iso())
        return {'status': 'success'}
    if row['status'] != 'capturing':
        return {'status': 'skipped'}
    try:
        if task.artifacts_summary['verify']:
            result = verify_auth_recovery(base_dir, task.target_id)
            incident = AuthRecoveryRepository(base_dir).get(task.target_id)
            now = current_iso()
            if not result['ok']:
                verification_failed_text(base_dir, incident, row, now=now, code=result['verification']['code'])
            stop_round(base_dir, incident, row, now=now)
            return {'status': 'success'}
        code = task.artifacts_summary['code']
        if row['codes_sent'] + 1 != code:
            return {'status': 'skipped'}
        dedupe_key = f"{task.target_id}:{row['round_no']}:{code}"
        existing = NotificationOutboxRepository(base_dir).by_dedupe(dedupe_key, channel='feishu')
        if existing is not None:
            # A crash after enqueue must not navigate/refresh the QR that was already delivered.
            repo.captured(row, expires_at=existing['expires_at'], lease_id=row['tab_lease_id'], now=current_iso())
            return {'status': 'success'}
        if code == 1:
            verification = verify_auth_recovery(base_dir, task.target_id)
            incident = cast(dict, AuthRecoveryRepository(base_dir).get(task.target_id))
            if verification['ok'] or not verification['verification']['login_required']:
                stop_round(base_dir, incident, row, now=current_iso(),
                    code=None if verification['ok'] else verification['verification']['code'])
                return {'status': 'success'}
        path = image_path(task.target_id, row['round_no'], code)
        capability = platform_definition(incident['platform']).qr_login
        try:
            lease_id = capture_qr(incident['platform'], row['tab_site'], path)
        except QrCaptureError as exc:
            if str(exc) == 'qr_element_not_found':
                verification = verify_auth_recovery(base_dir, task.target_id)
                incident = cast(dict, AuthRecoveryRepository(base_dir).get(task.target_id))
                if verification['ok']:
                    stop_round(base_dir, incident, row, now=current_iso())
                    return {'status': 'success'}
            raise
        now = current_iso()
        expiry = format_utc_seconds_z(parse_utc(now) + timedelta(seconds=capability.qr_ttl_seconds))
        # Re-read after the external action so a manual resolution cannot send a new code.
        incident = AuthRecoveryRepository(base_dir).get(task.target_id)
        if incident['status'] == 'resolved':
            delete_image(str(path))
            stop_round(base_dir, incident, row, now=now)
            return {'status': 'success'}
        code_label = str(code) if row['manual'] else f"{code}/{load_config()['auth_qr']['codes_per_round']}"
        authorization = '扫码会把你的账号登录到 InfoOS 专用浏览器'
        enqueue_notification(base_dir, kind=NotificationKind.AUTH_RECOVERY_QR,
            title=f"{platform_definition(incident['platform']).label} 需要重新登录",
            body=f"用 {capability.scan_app_label} 扫一扫（约 {capability.qr_ttl_seconds // 60} 分钟内有效，第 {code_label} 张）。{authorization}，请勿转发。过期后回复「{qr_menu_number(incident['platform'])}」换一张。",
            image_path=str(path), related=('auth_incident', task.target_id),
            dedupe_key=dedupe_key, expires_at=expiry, manual=bool(row['manual']), now=now)
        repo.captured(row, expires_at=expiry, lease_id=lease_id, now=now)
        return {'status': 'success'}
    except (QrCaptureError, OSError, TimeoutError, AuthRecoveryRuntimeUnavailable, AuthRecoveryNotActionable, AuthRecoverySuperseded) as exc:
        code = str(exc) if isinstance(exc, QrCaptureError) else 'qr_runtime_unavailable'
        if task.artifacts_summary['verify']:
            verification_failed_text(base_dir, incident, row, now=current_iso(), code=code)
        stop_round(base_dir, incident, row, now=current_iso(), code=code)
        return {'status': 'success', 'artifacts': {'reason': code}}


def qr_browser_unavailable(task, base_dir: Path) -> dict:
    row = AuthQrRepository(base_dir).get(task.target_id)
    incident = AuthRecoveryRepository(base_dir).get(task.target_id)
    if row is not None and incident is not None:
        stop_round(base_dir, incident, row, now=current_iso(), code='browser_session_not_ready')
    return {'status': 'success', 'artifacts': {'reason': 'browser_session_not_ready'}}
