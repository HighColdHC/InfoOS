from __future__ import annotations

import sqlite3

import pytest

from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.planner import enqueue_fetch_tasks_for_source, plan_fetch_tasks_for_source, plan_manual_fetch_task_for_source
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.auth_recovery import AuthRecoveryNotActionable, verify_auth_recovery
from backend.domain.task_resources import ResourceRequest
from backend.domain.timestamps import parse_utc
from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus
from backend.infrastructure.storage.auth_recovery_reconciliation import AuthRecoveryReconciliationRepository
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


NOW = '2026-07-23T00:00:00Z'
RECOVERED = '2026-07-23T01:00:00Z'


def _source(base_dir, source_id='one', gap=None):
    source = {
        'source_id': source_id, 'platform': 'zsxq', 'source_type': 'topics',
        'identifier': '123', 'display_name': source_id, 'cadence_hours': 1,
        'enabled': True, 'connector_status': 'supported', 'raw_metadata': {},
    }
    if gap is not None:
        source['raw_metadata']['fetch_rate_policy'] = {'min_gap_seconds': gap, 'jitter_seconds': 0}
    SourceRepository(base_dir).upsert_source(source)
    return SourceRepository(base_dir).get_source(source_id)


def _task(base_dir, task_id, source_id='one', *, manual=False, research=None, created=0, next_run_at=NOW):
    from backend.application.tasks.execution_clock import add_seconds
    artifacts = {'scheduled_for': NOW}
    if manual:
        artifacts['manual_run'] = True
    if research:
        artifacts['research_batch_id'] = research
    repo = TaskRepository.for_base_dir(base_dir)
    repo.enqueue(Task(task_id, TaskType.FETCH, 'source', source_id, next_run_at=next_run_at, artifacts_summary=artifacts))
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute('update tasks set created_at = ? where task_id = ?', (add_seconds(NOW, created), task_id))


def _manual_outage(base_dir):
    RuntimeCircuitRepository(base_dir).open_manual('platform', 'zsxq', occurred_at=NOW,
        reason_code='platform_not_logged_in', user_action='login_or_config_required')
    return AuthRecoveryReconciliationRepository(base_dir).reconcile_manual_login_circuits(updated_at=NOW)[0]


def _recover(base_dir, incident, monkeypatch):
    monkeypatch.setattr('backend.application.workflows.auth_recovery.probe_platform', lambda _: AccountState(status=AccountStatus.VERIFIED))
    return verify_auth_recovery(base_dir, incident['incident_id'], checked_at=RECOVERED)


@pytest.mark.parametrize('manual', [True, False])
def test_planner_does_not_accumulate_circuit_waiting_fetches_but_manual_still_enqueues(tmp_path, monkeypatch, manual):
    source = _source(tmp_path)
    monkeypatch.setattr('backend.application.tasks.planner.source_can_enter_fetch_queue', lambda _: True)
    _task(tmp_path, 'waiting')
    if manual:
        _manual_outage(tmp_path)
    else:
        RuntimeCircuitRepository(tmp_path).record_failure('platform', 'zsxq', occurred_at=NOW,
            policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=3600, probe_lease_seconds=120))
    # A changed input version must not materialize new periodic work either.
    changed = {**source, 'raw_metadata': {'changed': True}}
    for day in range(3):
        assert plan_fetch_tasks_for_source(tmp_path, changed, now=f'2026-07-{24 + day}T00:00:00Z') == []
        assert enqueue_fetch_tasks_for_source(tmp_path, changed, now=f'2026-07-{24 + day}T00:00:00Z') == []
    # Even a plan built before the circuit blocks the source is fenced at insertion.
    stale_plan = [Task('stale-plan', TaskType.FETCH, 'source', 'one', input_version='new')]
    repo = TaskRepository.for_base_dir(tmp_path)
    assert repo.enqueue_periodic_fetch_singleflight(stale_plan) == []
    user_task = plan_manual_fetch_task_for_source(source, now=RECOVERED, manual_run_id='user')
    assert user_task is not None
    repo.enqueue(user_task)
    assert repo.get(user_task.task_id).status is TaskStatus.PENDING
    assert len(repo.list_by_target(target_kind='source', target_id='one', task_type=TaskType.FETCH)) == 2


@pytest.mark.parametrize('manual_winner', [False, True])
def test_first_recovery_coalesces_30_historical_tasks_with_manual_priority(tmp_path, monkeypatch, manual_winner):
    _source(tmp_path)
    for index in range(30):
        _task(tmp_path, f'old-{index:02}', created=index, manual=manual_winner and index in (2, 5))
    incident = _manual_outage(tmp_path)
    result = _recover(tmp_path, incident, monkeypatch)
    assert result['ok'] is True
    assert result['incident']['recovered_task_count'] == 1
    winner_id = 'old-05' if manual_winner else 'old-29'
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = repo.list_by_target(target_kind='source', target_id='one', task_type=TaskType.FETCH)
    assert sum(task.status is TaskStatus.PENDING for task in tasks) == 1
    assert sum(task.status is TaskStatus.SKIPPED for task in tasks) == 29
    assert repo.get(winner_id).next_run_at == RECOVERED
    for task in tasks:
        if task.task_id == winner_id:
            continue
        assert task.error_reason == 'superseded'
        assert task.artifacts_summary['superseded_by'] == winner_id
        assert '被同来源更新的任务取代' in task.artifacts_summary['skip_reason']
        event = next(e for e in TaskEventRepository(tmp_path).list_events(task_id=task.task_id) if e['event_type'] == 'skipped')
        assert event['payload']['superseded_by'] == winner_id
    # A resolved incident cannot apply the release a second time.
    with pytest.raises(AuthRecoveryNotActionable):
        verify_auth_recovery(tmp_path, incident['incident_id'], checked_at=RECOVERED)


@pytest.mark.parametrize('gap', [None, 90])
def test_manual_recovery_staggers_three_sources_and_research_without_early_claims(tmp_path, monkeypatch, gap):
    for source_id in ('one', 'two', 'three', 'research'):
        _source(tmp_path, source_id, gap=gap)
        _task(tmp_path, source_id, source_id, research='research-batch' if source_id == 'research' else None)
    result = _recover(tmp_path, _manual_outage(tmp_path), monkeypatch)
    assert result['incident']['recovered_task_count'] == 4
    repo = TaskRepository.for_base_dir(tmp_path)
    pending = repo.list_pending()
    timestamps = sorted(parse_utc(task.next_run_at) for task in pending)
    assert all((right - left).total_seconds() >= (gap or 60) for left, right in zip(timestamps, timestamps[1:]))
    calls = []
    run_tasks_by_ids(tmp_path, [task.task_id for task in pending], now=RECOVERED,
        handlers={TaskType.FETCH: lambda task, _: calls.append(task.task_id) or {'status': 'success', 'artifacts': {}}})
    assert calls == ['research']
    assert sum(task.status is TaskStatus.PENDING for task in repo.list_by_target(target_kind='source', target_id='one')) == 1


def test_research_batches_and_captured_inputs_are_preserved_on_same_source(tmp_path, monkeypatch):
    _source(tmp_path)
    _task(tmp_path, 'scheduled-old')
    _task(tmp_path, 'scheduled-new', created=1)
    _task(tmp_path, 'manual-old', manual=True, created=2)
    _task(tmp_path, 'manual-new', manual=True, created=3)
    _task(tmp_path, 'research-a', research='batch-a', created=4)
    _task(tmp_path, 'research-b', research='batch-b', created=5)
    _task(tmp_path, 'research-a-second', research='batch-a', created=6)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task('captured', TaskType.FETCH, 'source', 'one', artifacts_summary={'capture_id': 'capture-one'}))
    result = _recover(tmp_path, _manual_outage(tmp_path), monkeypatch)
    assert result['incident']['recovered_task_count'] == 5
    assert {task.task_id for task in repo.list_pending()} == {'manual-new', 'research-a', 'research-a-second', 'research-b', 'captured'}


def test_automatic_probe_close_coalesces_and_staggers_pending_backlog(tmp_path):
    _source(tmp_path, gap=90)
    _source(tmp_path, 'two', gap=90)
    for index in range(30):
        _task(tmp_path, f'old-{index:02}', created=index)
    _task(tmp_path, 'manual', manual=True, created=1)
    _task(tmp_path, 'research', 'two', research='batch')
    circuits = RuntimeCircuitRepository(tmp_path)
    circuits.record_failure('platform', 'zsxq', occurred_at=NOW,
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=60, probe_lease_seconds=120))
    assert circuits.admit('platform', 'zsxq', owner_token='probe', now=RECOVERED).probe
    result = circuits.record_success('platform', 'zsxq', owner_token='probe', occurred_at=RECOVERED)
    assert result['transitioned'] is True
    repo = TaskRepository.for_base_dir(tmp_path)
    assert {task.task_id for task in repo.list_pending()} == {'manual', 'research'}
    slots = sorted(parse_utc(task.next_run_at) for task in repo.list_pending())
    assert (slots[0] - parse_utc(RECOVERED)).total_seconds() == 90
    assert (slots[1] - slots[0]).total_seconds() == 90
    assert sum(repo.get(f'old-{index:02}').status is TaskStatus.SKIPPED for index in range(30)) == 30
    # Both resource and ordinary claims must honor recovery times, including stale IDs.
    assert repo.claim_task_ids(['manual'], worker_id='early', now=RECOVERED, lease_expires_at='2026-07-23T02:00:00Z') == []
    assert repo.claim_task_ids_with_resources(['manual'], worker_id='early', now=RECOVERED,
        lease_expires_at='2026-07-23T02:00:00Z', resource_requests={'manual': ResourceRequest('browser_site', 'zsxq')}) == []
    claimed = repo.claim_task_ids(['manual'], worker_id='on-time', now=repo.get('manual').next_run_at, lease_expires_at='2026-07-23T02:00:00Z')
    assert len(claimed) == 1


def test_bulk_manual_submission_is_independent_of_circuit_waiting_tasks(tmp_path, monkeypatch):
    from backend.application.workflows.source_batch import enqueue_source_batch
    _source(tmp_path)
    monkeypatch.setattr('backend.application.tasks.planner.source_can_enter_fetch_queue', lambda _: True)
    _task(tmp_path, 'waiting')
    incident = _manual_outage(tmp_path)
    batch = enqueue_source_batch(tmp_path, now=RECOVERED)
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = repo.list_by_target(target_kind='source', target_id='one', task_type=TaskType.FETCH)
    submitted = next(task for task in tasks if task.task_id != 'waiting')
    assert submitted.artifacts_summary['manual_run'] is True
    assert batch['batch_type'] == 'source_run'
    _recover(tmp_path, incident, monkeypatch)
    assert repo.get(submitted.task_id).status is TaskStatus.PENDING
    assert repo.get('waiting').status is TaskStatus.SKIPPED


def test_historical_manual_batch_without_manual_artifact_wins_over_newer_periodic(tmp_path, monkeypatch):
    from backend.application.usecases.task_submission import submit_task_batch
    _source(tmp_path)
    _task(tmp_path, 'historical-manual', created=1)
    submit_task_batch(tmp_path, batch_type='source_run', existing_task_ids=['historical-manual'])
    _task(tmp_path, 'new-periodic', created=2)
    _recover(tmp_path, _manual_outage(tmp_path), monkeypatch)
    repo = TaskRepository.for_base_dir(tmp_path)
    assert repo.get('historical-manual').status is TaskStatus.PENDING
    assert repo.get('new-periodic').status is TaskStatus.SKIPPED


@pytest.mark.parametrize(('platform', 'metadata', 'expected'), [
    ('zsxq', {}, 60),
    ('zsxq', {'fetch_rate_policy': {'hourly_budget': 5}}, 60),
    ('zsxq', {'fetch_rate_policy': {'min_gap_seconds': 0}}, 1),
    ('x', {}, 12),
    ('youtube', {}, 8),
    ('x', {'fetch_rate_policy': {'enabled': False}}, 60),
])
def test_recovery_uses_existing_gap_or_default_without_same_time_slots(platform, metadata, expected):
    from backend.infrastructure.runtime.fetch_rate_policy import recovery_fetch_gap
    assert recovery_fetch_gap({'platform': platform, 'raw_metadata': metadata}) == expected
