"""Execute the service shell against controlled process/port command boundaries."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess

import pytest


RUN = Path(__file__).resolve().parents[3] / 'deploy/docker/rootfs/etc/services.d/opencli_daemon/run'
DAEMON = '/usr/local/lib/node_modules/@jackwener/opencli/dist/src/daemon.js'


def _script(path: Path, body: str) -> None:
    path.write_text('#!/bin/sh\nset -eu\n' + body)
    path.chmod(0o755)


@pytest.mark.parametrize(('identity', 'reclaimed'), [
    ('{uid} 1 /usr/local/bin/node ' + DAEMON, True),
    ('{uid} 42 /usr/local/bin/node ' + DAEMON, False),
    ('999999 1 /usr/local/bin/node ' + DAEMON, False),
    ('{uid} 1 /usr/local/bin/node /tmp/daemon.js', False),
    ('{uid} 1 /usr/local/bin/node ' + DAEMON + '.other', False),
    ('{uid} 1 /usr/local/bin/node ' + DAEMON + ' --other', False),
])
def test_run_only_terminates_matching_orphan_listener(tmp_path, identity, reclaimed):
    with subprocess.Popen(['sleep', '30']) as listener:
        commands = tmp_path / 'bin'
        commands.mkdir()
        started = tmp_path / 'started'
        _script(commands / 'ps', 'printf "%s\\n" "$PROCESS_IDENTITY"\n')
        # Model port ownership from the real child's state, including an unreaped zombie.
        _script(commands / 'lsof', f'''
state="$(/bin/ps -p {listener.pid} -o stat=)" || exit 1
case "$state" in *Z*|'') exit 1 ;; esac
printf '%s\\n' {listener.pid}
''')
        _script(commands / 'runner', f'printf "%s\\n" "$@" > "{started}"\n')
        run = tmp_path / 'run'
        run.write_text(RUN.read_text().replace('/opt/infoos/bin/infoos-run-service', str(commands / 'runner')))
        env = {**os.environ, 'PATH': f'{commands}:{os.environ["PATH"]}',
               'PROCESS_IDENTITY': identity.format(uid=os.getuid())}
        try:
            result = subprocess.run(['sh', str(run)], env=env, capture_output=True, text=True, timeout=15)
            assert result.returncode == 0, result.stderr
            assert started.read_text().splitlines() == ['opencli', '/usr/local/bin/node', DAEMON]
            if reclaimed:
                assert listener.wait(timeout=2) == -signal.SIGTERM
                assert 'reclaiming orphan daemon' in result.stdout
            else:
                assert listener.poll() is None
        finally:
            if listener.poll() is None:
                listener.terminate()


def test_run_waits_for_port_release_and_does_not_exec_on_timeout(tmp_path):
    with subprocess.Popen(['sleep', '30']) as listener:
        commands = tmp_path / 'bin'
        commands.mkdir()
        started = tmp_path / 'started'
        _script(commands / 'ps', f"printf '%s\\n' '{os.getuid()} 1 /usr/local/bin/node {DAEMON}'\n")
        _script(commands / 'lsof', f"printf '%s\\n' '{listener.pid}'\n")
        _script(commands / 'sleep', ':\n')
        _script(commands / 'runner', f'touch "{started}"\n')
        run = tmp_path / 'run'
        run.write_text(RUN.read_text().replace('/opt/infoos/bin/infoos-run-service', str(commands / 'runner')))
        try:
            result = subprocess.run(['sh', str(run)], capture_output=True, text=True, timeout=5,
                env={**os.environ, 'PATH': f'{commands}:{os.environ["PATH"]}'})
            assert result.returncode == 1
            assert 'port 19825 still occupied' in result.stderr
            assert not started.exists()
            assert listener.wait(timeout=2) == -signal.SIGTERM
        finally:
            if listener.poll() is None:
                listener.terminate()


def test_run_starts_when_no_listener_exists(tmp_path):
    commands = tmp_path / 'bin'
    commands.mkdir()
    started = tmp_path / 'started'
    _script(commands / 'lsof', 'exit 1\n')
    _script(commands / 'runner', f'touch "{started}"\n')
    run = tmp_path / 'run'
    run.write_text(RUN.read_text().replace('/opt/infoos/bin/infoos-run-service', str(commands / 'runner')))
    result = subprocess.run(['sh', str(run)], capture_output=True, text=True, timeout=5,
        env={**os.environ, 'PATH': f'{commands}:{os.environ["PATH"]}'})
    assert result.returncode == 0, result.stderr
    assert started.exists()
