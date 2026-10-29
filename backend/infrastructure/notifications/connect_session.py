"""Private setup state and cross-process locking for the detached connector."""
import fcntl
import json
import os
import signal
import subprocess
import tempfile
from contextlib import contextmanager

from backend.infrastructure.notifications.lark_cli import cli_home, prepare_directories

PUBLIC_FIELDS = ('step', 'status', 'expires_at', 'error_code', 'qr_version')
_CHILDREN: dict[int, subprocess.Popen] = {}


def setup_dir():
    return cli_home() / 'setup'


@contextmanager
def locked():
    prepare_directories()
    fd = os.open(setup_dir() / 'session.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def read() -> dict:
    path = setup_dir() / 'session.json'
    if not path.exists():
        return {'step': 'create_app', 'status': 'idle', 'expires_at': None,
                'error_code': None, 'qr_version': 0, 'pid': None, 'session_id': None}
    return json.loads(path.read_text(encoding='utf-8'))


def save(value: dict) -> None:
    fd, name = tempfile.mkstemp(dir=setup_dir(), prefix='.session-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        os.replace(name, setup_dir() / 'session.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def clear_qr() -> None:
    for name in ('connect-qr.png', 'connect-qr-next.png'):
        (setup_dir() / name).unlink(missing_ok=True)


def remember(child: subprocess.Popen) -> None:
    for pid, previous in list(_CHILDREN.items()):
        if previous.poll() is not None:
            _CHILDREN.pop(pid)
    _CHILDREN[child.pid] = child


def alive(pid: int | None) -> bool:
    if pid is None:
        return False
    child = _CHILDREN.get(pid)
    if child is not None and child.poll() is not None:
        _CHILDREN.pop(pid)
        return False
    try:
        return os.getpgid(pid) == pid
    except ProcessLookupError:
        return False


def stop(pid: int | None) -> None:
    if alive(pid):
        # The detached coordinator and every CLI child share this process group.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child = _CHILDREN.pop(pid, None)
    if child is not None:
        child.wait()


class SessionEnded(Exception):
    pass


def update(session_id: str, **changes) -> None:
    with locked():
        session = read()
        if session['session_id'] != session_id or session['status'] != 'waiting_scan':
            raise SessionEnded
        session.update(changes)
        save(session)
