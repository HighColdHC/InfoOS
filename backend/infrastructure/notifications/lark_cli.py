"""The only lark-cli process boundary; never log command arguments or output."""
import json
import logging
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Callable

from backend.infrastructure.runtime.environment import state_home

logger = logging.getLogger(__name__)
_CATEGORIES = {'validation', 'authentication', 'authorization', 'config', 'network', 'api', 'policy', 'internal'}


class LarkCliError(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        logger.warning('lark-cli: %s', code)


def cli_home() -> Path:
    return state_home() / 'lark-cli'


def configured() -> bool:
    return (cli_home() / 'config' / 'config.json').is_file()


def process_env() -> dict[str, str]:
    home = cli_home()
    return {
        'HOME': str(home), 'PATH': '/usr/local/bin:/usr/bin:/bin',
        'LARKSUITE_CLI_CONFIG_DIR': str(home / 'config'),
        'LARKSUITE_CLI_DATA_DIR': str(home / 'data'),
        'LARKSUITE_CLI_LOG_DIR': str(home / 'logs'),
        'LARKSUITE_CLI_NO_UPDATE_NOTIFIER': '1',
        'LARKSUITE_CLI_NO_SKILLS_NOTIFIER': '1',
    }


def prepare_directories() -> Path:
    home = cli_home()
    for path in (home, *(home / name for name in ('config', 'data', 'logs', 'setup'))):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return home


def _error(returncode: int, payload: dict) -> LarkCliError:
    if returncode == 10:
        return LarkCliError('lark_confirmation_required')
    error = payload.get('error', {})
    category = error.get('type') if isinstance(error, dict) else None
    if not isinstance(category, str):
        category = None
    if category == 'confirmation':
        return LarkCliError('lark_confirmation_required')
    if category == 'network' or returncode == 4:
        return LarkCliError('lark_network', retryable=True)
    fallback = {1: 'api', 2: 'validation', 3: 'authentication', 5: 'internal', 6: 'policy'}
    category = category if category in _CATEGORIES else fallback.get(returncode, 'internal')
    return LarkCliError(f'lark_{category}')


def parse_result(returncode: int, stdout: str) -> dict:
    try:
        value = json.loads(stdout)
    except ValueError:
        value = None
    payload = value if isinstance(value, dict) else {}
    if returncode == 10 or payload.get('ok') is False:
        raise _error(returncode, payload)
    if returncode != 0 and payload.get('ok') is not True:
        raise _error(returncode, payload)
    # Setup commands can succeed without JSON. Callers requiring fields validate them.
    if payload.get('ok') is True:
        data = payload.get('data', {})
        if not isinstance(data, dict):
            raise LarkCliError('lark_invalid_response')
        return data
    return payload


class LarkCli:
    def events(self) -> 'LarkEventStream':
        return LarkEventStream()

    def run(self, args: list[str], *, cwd: Path | None = None, timeout: float = 20,
            on_line: Callable[[str], None] | None = None) -> dict:
        """Stream only to an in-memory setup parser; discard all raw output on return."""
        home = prepare_directories()
        executable = os.environ.get('INFOOS_LARK_CLI', '/usr/local/bin/lark-cli')
        try:
            with subprocess.Popen([executable, *args], cwd=cwd or home, env=process_env(),
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT if on_line is not None else subprocess.PIPE) as process:
                try:
                    if on_line is None:
                        stdout, _ = process.communicate(timeout=timeout)
                        output = stdout.decode('utf-8', errors='replace')
                    else:
                        output = self._read(process, timeout, on_line)
                    return parse_result(process.returncode, output)
                except BaseException:
                    # Process boundary: kill and reap before propagating cancellation/errors.
                    process.kill()
                    process.wait()
                    raise
        except subprocess.TimeoutExpired:
            raise LarkCliError('lark_timeout', retryable=True) from None
        except FileNotFoundError:
            raise LarkCliError('lark_unavailable') from None
        except OSError:
            raise LarkCliError('lark_process_error') from None

    @staticmethod
    def _read(process: subprocess.Popen, timeout: float, on_line: Callable[[str], None] | None) -> str:
        deadline = time.monotonic() + timeout
        chunks, pending = [], b''
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LarkCliError('lark_timeout', retryable=True)
                for key, _ in selector.select(min(remaining, 0.25)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    chunks.append(chunk)
                    if on_line is not None:
                        pending += chunk
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            on_line(line.decode('utf-8', errors='replace'))
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return b''.join(chunks).decode('utf-8', errors='replace')


class LarkEventStream:
    """Keep consume's stdin open and gate NDJSON delivery on its ready marker."""
    def __init__(self):
        home = prepare_directories()
        executable = os.environ.get('INFOOS_LARK_CLI', '/usr/local/bin/lark-cli')
        try:
            self.process = subprocess.Popen(
                [executable, 'event', 'consume', 'im.message.receive_v1', '--as', 'bot'],
                cwd=home, env=process_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            raise LarkCliError('lark_unavailable') from None
        except OSError:
            raise LarkCliError('lark_process_error') from None
        self.ready = False
        self.invalid_lines = 0
        self.exit_reason = 'process_exit'
        self.error_code = None
        self._started = time.monotonic()
        self._selector = selectors.DefaultSelector()
        self._buffers = {'stdout': b'', 'stderr': b''}
        self._events = []
        self._selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
        self._selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')

    @property
    def finished(self) -> bool:
        return self.process.poll() is not None and not self._selector.get_map()

    def poll(self, timeout: float = 1) -> list[dict]:
        for key, _ in self._selector.select(timeout):
            chunk = os.read(key.fileobj.fileno(), 65536)
            name = key.data
            if not chunk:
                self._selector.unregister(key.fileobj)
                if self._buffers[name]:
                    self._line(name, self._buffers[name])
                    self._buffers[name] = b''
                continue
            self._buffers[name] += chunk
            while b'\n' in self._buffers[name]:
                line, self._buffers[name] = self._buffers[name].split(b'\n', 1)
                self._line(name, line)
        if not self.ready:
            if time.monotonic() - self._started > 30:
                raise LarkCliError('lark_event_ready_timeout', retryable=True)
            return []
        events, self._events = self._events, []
        return events

    def _line(self, name: str, raw: bytes) -> None:
        line = raw.decode('utf-8', errors='replace').strip()
        if name == 'stderr':
            if line == '[event] ready event_key=im.message.receive_v1':
                self.ready = True
            for reason in ('limit', 'timeout', 'signal'):
                if line.startswith('[event] exited') and f'reason: {reason}' in line:
                    self.exit_reason = reason
            try:
                payload = json.loads(line)
            except ValueError:
                return
            if isinstance(payload, dict) and isinstance(payload.get('error'), dict):
                category = payload['error'].get('type')
                self.error_code = f'lark_{category}' if isinstance(category, str) and category in _CATEGORIES else 'lark_internal'
            return
        try:
            event = json.loads(line)
        except ValueError:
            self.invalid_lines += 1
            return
        if not isinstance(event, dict):
            self.invalid_lines += 1
            return
        self._events.append(event)

    def close(self) -> None:
        try:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=10)
        finally:
            self._selector.close()
            self.process.stdout.close()
            self.process.stderr.close()
            self._events.clear()
