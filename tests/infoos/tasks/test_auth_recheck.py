from __future__ import annotations

import sqlite3

import pytest

from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.acquisition_failures import AcquisitionFailure, AcquisitionFailureError
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


NOW = '2026-07-23T00:00:00Z'


def _enqueue(base_dir, task_id, platform='zsxq'):
    source_id = f'{platform}-{task_id}'
    SourceRepository(base_dir).upsert_source({
        'source_id': source_id, 'platform': platform, 'source_type': 'topics',
        'identifier': '123', 'display_name': source_id, 'cadence_hours': 24,
        'enabled': True, 'connector_status': 'supported',
        'raw_metadata': {'fetch_rate_policy': {'enabled': False}},
    })
    TaskRepository.for_base_dir(base_dir).enqueue(Task(task_id, TaskType.FETCH, 'source', source_id))


def _fail_auth(*_args):
    raise AcquisitionFailureError(AcquisitionFailure(
        reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN, message='AUTH_REQUIRED',
        retryable=False, user_action='login', diagnostic={'category': 'auth_required'},
    ))


def _run(base_dir, task_id, now=NOW):
    return run_tasks_by_ids(base_dir, [task_id], now=now, handlers={TaskType.FETCH: _fail_auth})


def _evidence(base_dir, task_id):
    return next(event['payload']['auth_recheck'] for event in TaskEventRepository(base_dir).list_events(
        task_id=task_id,
    ) if 'auth_recheck' in event['payload'])


@pytest.mark.parametrize(('status', 'outcome', 'manual'), [
    (AccountStatus.VERIFIED, 'verified', False),
    (AccountStatus.NEEDS_LOGIN, 'not_logged_in', True),
    (AccountStatus.FAILED, 'unknown', False),
    (AccountStatus.SKIPPED, 'unknown', False),
    (None, 'unknown', False),
])
def test_auth_recheck_uses_admitted_resource_and_persists_outcome(tmp_path, monkeypatch, status, outcome, manual):
    _enqueue(tmp_path, 'auth')
    calls = []

    def probe(platform):
        calls.append(platform)
        with sqlite3.connect(TaskRepository.for_base_dir(tmp_path).db_path) as conn:
            assert conn.execute("select count(*) from runtime_resource_leases where task_id = 'auth' and resource_kind = 'browser_site'").fetchone()[0] == 1
            assert conn.execute("select status from tasks where task_id = 'auth'").fetchone()[0] == 'running'
        if status is None:
            raise TimeoutError('probe timed out; token=secret-token')
        return AccountState(status=status, error='Not logged in' if status is AccountStatus.NEEDS_LOGIN else 'probe detail')

    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', probe)
    monkeypatch.setattr('backend.application.workflows.auth_recovery.enqueue_notification', lambda *args, **kwargs: [])
    _run(tmp_path, 'auth')
    assert calls == ['zsxq']
    evidence = _evidence(tmp_path, 'auth')
    assert evidence['outcome'] == outcome
    assert 'secret-token' not in str(evidence)
    if outcome == 'unknown':
        assert '复查未完成' in evidence['summary']
    elif outcome == 'verified':
        assert '登录复查通过' in evidence['summary']
    repo = TaskRepository.for_base_dir(tmp_path)
    circuit = RuntimeCircuitRepository(tmp_path).get('platform', 'zsxq')
    assert circuit['recovery_mode'] == ('manual' if manual else 'automatic')
    assert repo.get('auth').status is (TaskStatus.BLOCKED if manual else TaskStatus.PENDING)
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute('select count(*) from auth_recovery_incidents').fetchone()[0] == int(manual)
    if not manual:
        assert repo.get('auth').last_failure_class == 'opencli_command_failed'
        assert repo.get('auth').next_run_at == '2026-07-23T00:00:30Z'
        assert repo.get('auth').artifacts_summary['user_action'] is None
        assert repo.get('auth').artifacts_summary['failure_diagnostic']['original_failure_reason'] == 'platform_not_logged_in'


def test_verified_recheck_uses_automatic_circuit_cooldown(tmp_path, monkeypatch):
    _enqueue(tmp_path, 'transient')
    monkeypatch.setenv('INFOOS_PLATFORM_CIRCUIT_FAILURE_THRESHOLD', '1')
    monkeypatch.setenv('INFOOS_PLATFORM_CIRCUIT_COOLDOWN_SECONDS', '90')
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', lambda _: AccountState(status=AccountStatus.VERIFIED))
    _run(tmp_path, 'transient')
    circuit = RuntimeCircuitRepository(tmp_path).get('platform', 'zsxq')
    assert circuit['state'] == 'open'
    assert circuit['recovery_mode'] == 'automatic'
    assert circuit['cooldown_until'] == '2026-07-23T00:01:30Z'


@pytest.mark.parametrize(('elapsed', 'window', 'calls', 'outcome'), [
    (599, None, 1, 'suppressed'),
    (601, None, 2, 'verified'),
    (61, '60', 2, 'verified'),
])
def test_recent_verified_recheck_is_durable_and_window_is_configurable(tmp_path, monkeypatch, elapsed, window, calls, outcome):
    from backend.application.tasks.execution_clock import add_seconds
    _enqueue(tmp_path, 'first')
    _enqueue(tmp_path, 'second')
    probes = []
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', lambda platform: probes.append(platform) or AccountState(status=AccountStatus.VERIFIED))
    if window:
        monkeypatch.setenv('INFOOS_AUTH_RECHECK_WINDOW_SECONDS', window)
    _run(tmp_path, 'first')
    _run(tmp_path, 'second', now=add_seconds(NOW, elapsed))
    assert len(probes) == calls
    assert _evidence(tmp_path, 'second')['outcome'] == outcome
    if outcome == 'suppressed':
        assert RuntimeCircuitRepository(tmp_path).get('platform', 'zsxq')['recovery_mode'] == 'manual'


@pytest.mark.parametrize('platform', ['x', 'youtube', 'reddit', 'xiaohongshu', 'douyin', 'zsxq'])
def test_existing_probe_platforms_support_fetch_recheck(tmp_path, monkeypatch, platform):
    _enqueue(tmp_path, 'supported', platform=platform)
    calls = []
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', lambda platform: calls.append(platform) or AccountState(status=AccountStatus.VERIFIED))
    _run(tmp_path, 'supported')
    assert calls == [platform]
    assert _evidence(tmp_path, 'supported')['outcome'] == 'verified'


def test_unsupported_platform_preserves_manual_recovery_without_probe(tmp_path, monkeypatch):
    _enqueue(tmp_path, 'unsupported', platform='github')
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', lambda _: pytest.fail('unsupported platform must not probe'))
    _run(tmp_path, 'unsupported')
    assert _evidence(tmp_path, 'unsupported')['outcome'] == 'unsupported'
    assert RuntimeCircuitRepository(tmp_path).get('platform', 'github')['recovery_mode'] == 'manual'


def test_unavailable_browser_from_probe_is_unconfirmed(tmp_path, monkeypatch):
    _enqueue(tmp_path, 'unavailable')
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', lambda _: AccountState(
        status=AccountStatus.NEEDS_LOGIN, error='Browser profile infoos-fetch is not connected',
    ))
    _run(tmp_path, 'unavailable')
    assert _evidence(tmp_path, 'unavailable')['outcome'] == 'unknown'


@pytest.mark.parametrize('failure', ['tab_lease', 'resource_lost'])
def test_recheck_resource_failure_remains_unconfirmed(tmp_path, monkeypatch, failure):
    _enqueue(tmp_path, 'lease-failure')
    probes = []

    def probe(platform):
        probes.append(platform)
        if failure == 'resource_lost':
            with sqlite3.connect(TaskRepository.for_base_dir(tmp_path).db_path) as conn:
                conn.execute("delete from runtime_resource_leases where task_id = 'lease-failure'")
        return AccountState(status=AccountStatus.VERIFIED)

    if failure == 'tab_lease':
        from contextlib import contextmanager

        @contextmanager
        def unavailable_lease(*_args, **_kwargs):
            raise TimeoutError('site lease unavailable')
            yield  # pragma: no cover

        monkeypatch.setattr('backend.application.tasks.auth_recheck.opencli_tab_lease', unavailable_lease)
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', probe)
    _run(tmp_path, 'lease-failure')
    assert _evidence(tmp_path, 'lease-failure')['outcome'] == 'unknown'
    assert len(probes) == int(failure != 'tab_lease')
    assert RuntimeCircuitRepository(tmp_path).get('platform', 'zsxq')['recovery_mode'] == 'automatic'


def test_unknown_recheck_exhausts_normal_retry_budget(tmp_path, monkeypatch):
    from backend.application.tasks.execution_clock import add_seconds
    _enqueue(tmp_path, 'exhausted', platform='xiaohongshu')
    monkeypatch.setenv('INFOOS_PLATFORM_CIRCUIT_FAILURE_THRESHOLD', '99')
    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', lambda _: AccountState(status=AccountStatus.FAILED))
    repo = TaskRepository.for_base_dir(tmp_path)
    now = NOW
    for attempt, delay in enumerate((30, 120, None), start=2):
        _run(tmp_path, 'exhausted', now=now)
        task = repo.get('exhausted')
        assert task.attempt == attempt
        assert task.last_failure_class == 'opencli_command_failed'
        assert task.status is (TaskStatus.FAILED if delay is None else TaskStatus.PENDING)
        assert task.next_run_at == (None if delay is None else add_seconds(now, delay))
        now = task.next_run_at or now
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute('select count(*) from auth_recovery_incidents').fetchone()[0] == 0


@pytest.mark.parametrize('stop_during_probe', [False, True])
def test_draining_worker_does_not_open_login_circuit(tmp_path, monkeypatch, stop_during_probe):
    import threading
    from backend.application.tasks.continuous_executor import run_continuous_tasks
    _enqueue(tmp_path, 'draining', platform='xiaohongshu')
    stopping = threading.Event()
    calls = []

    def probe(platform):
        calls.append(platform)
        stopping.set()
        return AccountState(status=AccountStatus.NEEDS_LOGIN, error='Not logged in')

    def fail(task, base_dir):
        if not stop_during_probe:
            stopping.set()
        _fail_auth()

    monkeypatch.setattr('backend.application.tasks.auth_recheck.probe_platform', probe)
    run_continuous_tasks(tmp_path, queue_name='browser_fetch', stop_event=stopping,
        max_workers=1, poll_seconds=0.01, handlers={TaskType.FETCH: fail})
    assert len(calls) == int(stop_during_probe)
    assert _evidence(tmp_path, 'draining')['outcome'] == 'unknown'
    assert TaskRepository.for_base_dir(tmp_path).get('draining').status is TaskStatus.PENDING
    assert RuntimeCircuitRepository(tmp_path).get('platform', 'xiaohongshu')['recovery_mode'] == 'automatic'
    with sqlite3.connect(TaskRepository.for_base_dir(tmp_path).db_path) as conn:
        assert conn.execute('select count(*) from auth_recovery_incidents').fetchone()[0] == 0
