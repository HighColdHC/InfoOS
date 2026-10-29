"""Executable CLI double. All state is synthetic and under pytest's temporary tree."""
import json
import sys
import time
from pathlib import Path

import pytest

from backend.infrastructure.notifications import connect_session as sessions
from backend.infrastructure.notifications.lark_cli import prepare_directories

SCRIPT = r'''
import base64, json, os, sys, time
from pathlib import Path
root = Path(__file__).parent
control_path = root / 'control.json'
control = json.loads(control_path.read_text())
args = sys.argv[1:]
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps({'args': args, 'env': dict(os.environ), 'cwd': os.getcwd(), 'pid': os.getpid()}) + '\n')
home = Path(os.environ['HOME'])
def emit(value, code=0):
    print(json.dumps(value), flush=True)
    sys.exit(code)
def wait_for(name):
    while not (root / name).exists():
        time.sleep(0.02)
if args[:2] == ['config', 'init']:
    if control.get('init_error'):
        emit({'ok': False, 'error': {'type': 'network', 'message': 'never-log-raw'}}, 4)
    print('https://open.feishu.cn/page/cli?user_code=synthetic-code', flush=True)
    wait_for('app_scan')
    (home / 'config' / 'config.json').write_text('{}')
    sys.exit(0)
if args[:2] == ['auth', 'status']:
    emit({'appId': 'synthetic-app', 'identities': {'user': {'available': (root / 'logged_in').exists(), 'openId': 'ou_synthetic'}}})
if args[:2] == ['auth', 'login']:
    if '--no-wait' in args:
        emit({'verification_url': 'https://fixture.invalid/verify?code=synthetic', 'device_code': 'synthetic-device-code', 'expires_in': 120})
    wait_for('user_scan')
    (root / 'logged_in').touch()
    sys.exit(0)
if args[:2] == ['auth', 'qrcode']:
    Path(args[args.index('--output') + 1]).write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
    sys.exit(0)
if args[:2] == ['event', 'consume']:
    if control.get('event_before_ready'):
        print(json.dumps(control['event_before_ready']), flush=True)
    if control.get('event_ready_gate'):
        wait_for('event_connect')
    time.sleep(control.get('ready_delay', 0))
    if control.get('event_ready', True):
        print('[event] ready event_key=im.message.receive_v1', file=sys.stderr, flush=True)
        print('[source] feishu-websocket: connected', file=sys.stderr, flush=True)
    for value in control.get('events', []):
        print(value if isinstance(value, str) else json.dumps(value), flush=True)
    if control.get('event_exit') is not None:
        sys.exit(control['event_exit'])
    sys.stdin.read()
    print('[event] exited — received 1 event(s) in 1s (reason: signal)', file=sys.stderr, flush=True)
    sys.exit(0)
if args[:2] == ['im', '+messages-send']:
    time.sleep(control.get('delay', 0))
    results = control.get('send_results', [])
    result = results.pop(0) if results else {'ok': True, 'data': {'message_id': 'synthetic-message'}}
    control['send_results'] = results
    control_path.write_text(json.dumps(control))
    print('synthetic warning on stderr', file=sys.stderr)
    emit(result.get('payload', result), result.get('exit', 0))
raise SystemExit(2)
'''


class FakeLark:
    def __init__(self, root: Path):
        self.root = root
        self.command = root / 'lark-cli'
        self.command.write_text(f'#!{sys.executable}\n' + SCRIPT)
        self.command.chmod(0o755)
        self.control()

    def control(self, **value):
        (self.root / 'control.json').write_text(json.dumps(value))

    def scan(self, step):
        (self.root / step).touch()

    def app(self):
        (prepare_directories() / 'config' / 'config.json').write_text('{}')

    def logged_in(self):
        self.app()
        self.scan('logged_in')

    def calls(self):
        path = self.root / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def eventually(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.03)
    raise AssertionError('synthetic CLI did not reach expected state')


@pytest.fixture
def fake_lark(tmp_path, monkeypatch):
    root = tmp_path / 'fake-cli'
    root.mkdir()
    fake = FakeLark(root)
    monkeypatch.setenv('INFOOS_LARK_CLI', str(fake.command))
    monkeypatch.setenv('UNRELATED_SECRET', 'must-not-inherit')
    yield fake
    with sessions.locked():
        session = sessions.read()
        sessions.stop(session['pid'])
    for child in list(sessions._CHILDREN.values()):
        child.wait(timeout=5)
    sessions._CHILDREN.clear()
