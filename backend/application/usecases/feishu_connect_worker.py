"""Detached process entry point for the two-scan lark-cli setup workflow."""
import os
import re
import sys
import time
from datetime import timedelta

from backend.application.usecases.notifications import enqueue_notification
from backend.domain.notifications import NotificationKind
from backend.domain.timestamps import utc_now_seconds_z, parse_utc, format_utc_seconds_z
from backend.infrastructure.notifications import connect_session as sessions
from backend.infrastructure.notifications.config import load_config, save_config
from backend.infrastructure.notifications.lark_cli import LarkCli, LarkCliError, configured

_APP_LINK = re.compile(r'https://open\.feishu\.cn/page/cli\?user_code=[^\s\x1b]+')


def _user(cli: LarkCli) -> str | None:
    result = cli.run(['auth', 'status', '--json'])
    try:
        user = result['identities']['user']
        if user['available'] is not True:
            return None
        open_id = user['openId']
        if not isinstance(open_id, str) or not open_id.startswith('ou_'):
            raise ValueError
        return open_id
    except (KeyError, TypeError, ValueError):
        raise LarkCliError('lark_invalid_response') from None


def _qr(cli: LarkCli, session_id: str, url: str, *, step: str, expires: int) -> None:
    directory = sessions.setup_dir()
    cli.run(['auth', 'qrcode', url, '--output', 'connect-qr-next.png', '--size', '512'], cwd=directory)
    with sessions.locked():
        session = sessions.read()
        if session['session_id'] != session_id or session['status'] != 'waiting_scan':
            raise sessions.SessionEnded
        image = directory / 'connect-qr-next.png'
        image.chmod(0o600)
        os.replace(image, directory / 'connect-qr.png')
        session.update(step=step, qr_version=int(time.time() * 1000),
            expires_at=format_utc_seconds_z(parse_utc(utc_now_seconds_z()) + timedelta(seconds=expires)))
        sessions.save(session)


def run_connection(base_dir: str, session_id: str) -> None:
    cli = LarkCli()
    try:
        if not configured():
            shown = False

            def app_line(line: str) -> None:
                nonlocal shown
                match = _APP_LINK.search(line)
                if match is not None and not shown:
                    _qr(cli, session_id, match[0], step='create_app', expires=600)
                    shown = True

            cli.run(['config', 'init', '--new', '--brand', 'feishu'], timeout=600, on_line=app_line)
            if not configured():
                raise LarkCliError('lark_not_configured')
        open_id = _user(cli)
        if open_id is None:
            with sessions.locked():
                sessions.clear_qr()
            sessions.update(session_id, step='authorize_user', qr_version=0)
            result = cli.run(['auth', 'login', '--no-wait', '--json', '--scope', 'offline_access'])
            try:
                url, code, expires = result['verification_url'], result['device_code'], result['expires_in']
                if not isinstance(url, str) or not isinstance(code, str) or not code or not isinstance(expires, int) or expires <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise LarkCliError('lark_invalid_response') from None
            _qr(cli, session_id, url, step='authorize_user', expires=expires)
            cli.run(['auth', 'login', '--device-code', code], timeout=expires)
            open_id = _user(cli)
            if open_id is None:
                raise LarkCliError('lark_user_not_authorized')
        # Serialize cancellation with binding and enqueuing. Never save the CLI status payload.
        with sessions.locked():
            session = sessions.read()
            if session['session_id'] != session_id or session['status'] != 'waiting_scan':
                raise sessions.SessionEnded
            config = load_config()
            config['enabled'] = True
            config['channels']['feishu'].update(enabled=True, receive_id=open_id)
            save_config(config)
            enqueue_notification(base_dir, kind=NotificationKind.CHANNEL_TEST, title='InfoOS 通知测试',
                body='飞书已连接，接收人已绑定。', dedupe_key=f'feishu-connect:{session_id}')
            sessions.clear_qr()
            session.update(step='done', status='completed', expires_at=None, error_code=None, pid=None)
            sessions.save(session)
    except sessions.SessionEnded:
        return
    except (LarkCliError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, LarkCliError) else 'lark_setup_storage_error'
        try:
            with sessions.locked():
                session = sessions.read()
                if session['session_id'] != session_id or session['status'] != 'waiting_scan':
                    raise sessions.SessionEnded
                session.update(status='failed', error_code=code, pid=None)
                sessions.clear_qr()
                sessions.save(session)
        except sessions.SessionEnded:
            return


if __name__ == '__main__':
    run_connection(sys.argv[1], sys.argv[2])
