"""RSS feed isolation through both FETCH execution entry points."""
from __future__ import annotations

import pytest

from backend.application.setup.verify import supports_platform_probe
from backend.application.tasks.admission import platform_scope_for_task
from backend.application.tasks.continuous_task_execution import _execute_claimed
from backend.application.tasks.execution_clock import add_seconds
from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.repository import TaskRepository
from backend.application.workflows.acquisition_failures import AcquisitionFailure, AcquisitionFailureError
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.infrastructure.storage.auth_recovery_reconciliation import AuthRecoveryReconciliationRepository
from backend.infrastructure.storage.platform_fetch_recovery import source_has_circuit_waiting_fetch
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


NOW = "2026-10-20T00:00:00Z"


def _source(base, source_id, *, platform="rss", source_type="feed", metadata=None):
    SourceRepository(base).upsert_source({
        "source_id": source_id, "platform": platform, "source_type": source_type,
        "identifier": source_id, "display_name": f"订阅 {source_id}",
        "cadence_hours": 24, "enabled": True, "connector_status": "supported",
        "raw_metadata": metadata or {"fetch_rate_policy": {"enabled": False}},
    })


def _timeout(*_args):
    raise AcquisitionFailureError(AcquisitionFailure(
        reason=FetchFailureReason.NETWORK_TIMEOUT, message="timeout",
        retryable=True, user_action=None, diagnostic={},
    ))


def _success(*_args):
    return {"status": "success", "artifacts": {}}


def _run(base, task_id, now, handler, driver, monkeypatch):
    monkeypatch.setattr("backend.application.tasks.continuous_task_execution.current_iso", lambda: now)
    monkeypatch.setattr("backend.application.tasks.processor_execution.current_iso", lambda: now)
    if driver == "executor":
        return run_tasks_by_ids(base, [task_id], now=now, handlers={TaskType.FETCH: handler})
    repo = TaskRepository.for_base_dir(base)
    claimed = repo.claim_task_ids([task_id], worker_id="rss-worker", now=now,
                                 lease_expires_at=add_seconds(now, 300))
    assert len(claimed) == 1
    return _execute_claimed(claimed[0], base_path=base, worker_id="rss-worker",
                            lease_seconds=300, resource=None, handlers={TaskType.FETCH: handler})


@pytest.mark.parametrize("driver", ["executor", "continuous"])
@pytest.mark.parametrize("probe_success", [True, False])
def test_rss_failure_isolation_and_half_open_probe(tmp_path, monkeypatch, driver, probe_success):
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_COOLDOWN_SECONDS", "60")
    for source_id in ("A", "B"):
        _source(tmp_path, source_id)
    repo = TaskRepository.for_base_dir(tmp_path)
    circuits = RuntimeCircuitRepository(tmp_path)
    # Keep the historical platform/rss row; no new observation may change it.
    circuits.record_success("platform", "rss", occurred_at=NOW)
    historical = circuits.get("platform", "rss")
    for index in range(2):
        task_id = f"A-failure-{index}"
        repo.enqueue(Task(task_id, TaskType.FETCH, "source", "A"))
        _run(tmp_path, task_id, add_seconds(NOW, index), _timeout, driver, monkeypatch)
        row = circuits.get("platform", "rss:A")
        assert row["state"] == ("closed" if index == 0 else "open")
        assert row["consecutive_failures"] == index + 1
    repo.enqueue(Task("B-success", TaskType.FETCH, "source", "B"))
    repo.enqueue(Task("A-wait", TaskType.FETCH, "source", "A"))
    _run(tmp_path, "A-wait", add_seconds(NOW, 2), _timeout, driver, monkeypatch)
    waiting = repo.get("A-wait")
    assert waiting.status is TaskStatus.PENDING and waiting.attempt == 1
    assert waiting.next_run_at == add_seconds(NOW, 61)
    _run(tmp_path, "B-success", add_seconds(NOW, 2), _success, driver, monkeypatch)
    assert repo.get("B-success").status is TaskStatus.SUCCESS
    assert circuits.get("platform", "rss:B")["state"] == "closed"
    assert circuits.get("platform", "rss:A")["state"] == "open"
    assert circuits.get("platform", "rss") == historical
    opened = next(event for event in TaskEventRepository(tmp_path).list_events(task_id="A-failure-1")
                  if event["event_type"] == "circuit_opened")
    assert opened["payload"]["scope_key"] == "rss:A"
    repo.enqueue(Task("A-probe", TaskType.FETCH, "source", "A"))

    def probe(*args):
        assert circuits.get("platform", "rss:A")["state"] == "half_open"
        assert not circuits.admit("platform", "rss:A", owner_token="other",
                                  now=add_seconds(NOW, 61)).allowed
        return _success(*args) if probe_success else _timeout(*args)

    _run(tmp_path, "A-probe", add_seconds(NOW, 61), probe, driver, monkeypatch)
    state = circuits.get("platform", "rss:A")
    assert state["state"] == ("closed" if probe_success else "open")
    assert state["consecutive_failures"] == (0 if probe_success else 3)
    if not probe_success:
        assert state["cooldown_until"] == add_seconds(NOW, 121)
    assert circuits.get("platform", "rss") == historical


@pytest.mark.parametrize("source_type", ["releases", "commits"])
@pytest.mark.parametrize("driver", ["executor", "continuous"])
def test_github_feed_transport_still_uses_platform_circuit(tmp_path, monkeypatch, source_type, driver):
    _source(tmp_path, "github-one", platform="github", source_type=source_type)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("github-timeout", TaskType.FETCH, "source", "github-one"))
    _run(tmp_path, "github-timeout", NOW, _timeout, driver, monkeypatch)
    circuits = RuntimeCircuitRepository(tmp_path)
    assert circuits.get("platform", "github")["consecutive_failures"] == 1
    assert circuits.get("platform", "rss:github-one") is None


def test_rss_recovery_only_coalesces_and_staggers_its_source(tmp_path, monkeypatch):
    # The gap must use the actual rss platform, not an env prefix containing rss:A.
    monkeypatch.setenv("INFOOS_RSS_FETCH_MIN_GAP_SECONDS", "90")
    for source_id in ("A", "B"):
        _source(tmp_path, source_id, metadata={"fetch_rate_policy": {"hourly_budget": 5}})
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id, source_id in (("A-old", "A"), ("A-new", "A"), ("B-old", "B"), ("B-new", "B")):
        repo.enqueue(Task(task_id, TaskType.FETCH, "source", source_id, next_run_at=NOW))
    before_b = [repo.get(task_id) for task_id in ("B-old", "B-new")]
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=60)
    # A legacy platform/rss row must no longer suppress RSS feed planning.
    circuits.record_failure("platform", "rss", occurred_at=NOW, policy=policy)
    with repo._connect_existing() as conn:
        assert not source_has_circuit_waiting_fetch(conn, "A")
        assert not source_has_circuit_waiting_fetch(conn, "B")
    circuits.record_failure("platform", "rss:A", occurred_at=NOW, policy=policy)
    with repo._connect_existing() as conn:
        assert source_has_circuit_waiting_fetch(conn, "A")
        assert not source_has_circuit_waiting_fetch(conn, "B")
    assert not supports_platform_probe("rss:A")
    assert AuthRecoveryReconciliationRepository(tmp_path).reconcile_manual_login_circuits(updated_at=NOW) == []
    assert circuits.admit("platform", "rss:A", owner_token="probe", now=add_seconds(NOW, 60)).probe
    circuits.record_success("platform", "rss:A", owner_token="probe", occurred_at=add_seconds(NOW, 60))
    assert repo.get("A-old").status is TaskStatus.SKIPPED
    assert repo.get("A-new").status is TaskStatus.PENDING
    assert repo.get("A-new").next_run_at == add_seconds(NOW, 150)
    assert [repo.get(task_id) for task_id in ("B-old", "B-new")] == before_b
    assert not TaskEventRepository(tmp_path).list_events(task_id="B-old")
    with repo._connect_existing() as conn:
        assert not source_has_circuit_waiting_fetch(conn, "A")


def test_scope_only_changes_rss_feed_fetch(tmp_path):
    _source(tmp_path, "rss-other", source_type="other")
    assert platform_scope_for_task(Task("other", TaskType.FETCH, "source", "rss-other"), tmp_path) == "rss"
    assert platform_scope_for_task(Task("build", TaskType.CARD_BUILD, "source", "rss-other"), tmp_path) is None
