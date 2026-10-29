"""LAN setup actions; the detached workflow survives web process restarts."""
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from backend.domain.timestamps import utc_now_seconds_z, parse_utc, format_utc_seconds_z
from backend.infrastructure.notifications import connect_session as sessions
from backend.infrastructure.notifications import rules
from backend.infrastructure.notifications.lark_cli import process_env, configured
from backend.infrastructure.runtime.environment import state_home
from backend.infrastructure.storage.paths import resolve_data_dir


def _refresh() -> dict:
    session = sessions.read()
    if session['status'] == 'waiting_scan':
        expired = parse_utc(session['expires_at']) <= parse_utc(utc_now_seconds_z())
        if expired or not sessions.alive(session['pid']):
            sessions.stop(session['pid'])
            session.update(status='failed', error_code='lark_setup_expired' if expired else 'lark_setup_interrupted', pid=None)
            sessions.clear_qr()
            sessions.save(session)
    return session


def _public(session: dict) -> dict:
    return {key: session[key] for key in sessions.PUBLIC_FIELDS}


def get_connection() -> dict:
    with sessions.locked():
        return _public(_refresh())


def start_connection(base_dir: Path | str) -> dict:
    with sessions.locked():
        session = _refresh()
        if session['status'] == 'waiting_scan':
            return _public(session)
        sessions.clear_qr()
        session = {'step': 'authorize_user' if configured() else 'create_app', 'status': 'waiting_scan',
                   'expires_at': format_utc_seconds_z(parse_utc(utc_now_seconds_z()) + timedelta(minutes=10)),
                   'error_code': None, 'qr_version': 0, 'pid': None, 'session_id': uuid4().hex}
        env = {**process_env(), 'INFOOS_STATE_HOME': str(state_home()),
               'INFOOS_DATA_HOME': str(resolve_data_dir(base_dir).resolve()),
               'INFOOS_NOTIFICATIONS_CONFIG': str(rules.DEFAULT_CONFIG_PATH)}
        if 'INFOOS_LARK_CLI' in os.environ:
            env['INFOOS_LARK_CLI'] = os.environ['INFOOS_LARK_CLI']
        try:
            child = subprocess.Popen([sys.executable, '-m', 'backend.application.usecases.feishu_connect_worker',
                                      str(Path(base_dir).resolve()) if Path(base_dir) != Path('.') else '.', session['session_id']],
                cwd=Path(__file__).resolve().parents[3], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            session.update(status='failed', error_code='lark_process_error')
        else:
            session['pid'] = child.pid
            sessions.remember(child)
        sessions.save(session)
        return _public(session)


def cancel_connection() -> dict:
    with sessions.locked():
        session = sessions.read()
        sessions.stop(session['pid'])
        sessions.clear_qr()
        session.update(status='idle', expires_at=None, error_code=None, pid=None, qr_version=0)
        sessions.save(session)
        return _public(session)


def connection_qr() -> bytes:
    with sessions.locked():
        session = _refresh()
        path = sessions.setup_dir() / 'connect-qr.png'
        if session['status'] != 'waiting_scan' or not path.is_file():
            raise FileNotFoundError
        return path.read_bytes()
