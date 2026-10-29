from __future__ import annotations

import sqlite3

import pytest

from backend.application.tasks import platform_circuits
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.processor import persist_task_failure
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.application.workflows.acquisition_failures import (
    AcquisitionFailure,
    AcquisitionFailureError,
)
from backend.domain.failures.taxonomy import FetchFailureReason


def _source(tmp_path, source_id: str, platform: str) -> None:
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": source_id,
            "platform": platform,
            "source_type": "feed" if platform == "rss" else "creator",
            "identifier": source_id,
            "display_name": source_id,
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
        }
    )


def test_open_platform_circuit_defers_without_attempt_and_other_platform_runs(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_COOLDOWN_SECONDS", "60")
    _source(tmp_path, "x-one", "x")
    _source(tmp_path, "x-two", "x")
    _source(tmp_path, "rss-one", "rss")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("x-fails", TaskType.FETCH, "source", "x-one"))
    run_due_tasks(
        tmp_path,
        now="2026-07-23T00:00:00Z",
        handlers={
            TaskType.FETCH: lambda *_: (_ for _ in ()).throw(
                AcquisitionFailureError(
                    AcquisitionFailure(
                        reason=FetchFailureReason.NETWORK_TIMEOUT,
                        message="network",
                        retryable=True,
                        user_action=None,
                        diagnostic={"category": "network"},
                    )
                )
            )
        },
    )
    repo.enqueue(Task("x-deferred", TaskType.FETCH, "source", "x-two"))
    repo.enqueue(Task("rss-runs", TaskType.FETCH, "source", "rss-one"))

    calls: list[str] = []
    result = run_due_tasks(
        tmp_path,
        now="2026-07-23T00:00:01Z",
        handlers={
            TaskType.FETCH: lambda task, *_: calls.append(task.task_id)
            or {"status": "success", "artifacts": {}}
        },
    )

    deferred = repo.get("x-deferred")
    assert deferred is not None and deferred.status is TaskStatus.PENDING
    assert deferred.attempt == 1
    assert deferred.last_failure_class is None
    assert calls == ["rss-runs"]
    assert result["succeeded"] == 1
    opened = next(
        event
        for event in TaskEventRepository(tmp_path).list_events(task_id="x-fails")
        if event["event_type"] == "circuit_opened"
    )
    assert opened["payload"] == {
        "version": 1,
        "scope_kind": "platform",
        "scope_key": "x",
        "reason_code": "network_timeout",
    }


def test_platform_half_open_admits_only_one_probe_and_success_closes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_FAILURE_THRESHOLD", "1")
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_COOLDOWN_SECONDS", "1")
    _source(tmp_path, "x-one", "x")
    _source(tmp_path, "x-two", "x")
    circuits = RuntimeCircuitRepository(tmp_path)
    circuits.record_failure(
        "platform",
        "x",
        occurred_at="2026-07-23T00:00:00Z",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("probe-one", TaskType.FETCH, "source", "x-one"))
    repo.enqueue(Task("probe-two", TaskType.FETCH, "source", "x-two"))
    calls: list[str] = []

    run_due_tasks(
        tmp_path,
        now="2026-07-23T00:00:01Z",
        handlers={
            TaskType.FETCH: lambda task, *_: calls.append(task.task_id)
            or {"status": "success", "artifacts": {}}
        },
    )

    assert len(calls) == 1
    assert circuits.get("platform", "x")["state"] == "closed"
    remaining = repo.get(next(iter({"probe-one", "probe-two"} - set(calls))))
    assert remaining is not None and remaining.status is TaskStatus.PENDING


def test_platform_parse_failure_does_not_trip_circuit(tmp_path) -> None:
    _source(tmp_path, "x-one", "x")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("parse-fails", TaskType.FETCH, "source", "x-one"))
    failure = AcquisitionFailure(
        reason=FetchFailureReason.PARSE_FAILED,
        message="bad content",
        retryable=False,
        user_action=None,
        diagnostic={},
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.FETCH: lambda *_: (_ for _ in ()).throw(
                AcquisitionFailureError(failure)
            )
        },
    )

    assert RuntimeCircuitRepository(tmp_path).get("platform", "x") is None


def test_platform_auth_failure_opens_manual_recovery_circuit(tmp_path, monkeypatch) -> None:
    # The circuit contract consumes a confirmed auth failure; probe behavior has its own suite.
    monkeypatch.setattr("backend.application.tasks.auth_recheck.recheck_auth_failure",
                        lambda error, *args, **kwargs: error)
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    _source(tmp_path, "x-one", "x")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("auth-fails", TaskType.FETCH, "source", "x-one"))
    failure = AcquisitionFailure(
        reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
        message="cookie secret must not persist in circuit",
        retryable=False,
        user_action="login",
        diagnostic={},
    )

    run_due_tasks(
        tmp_path,
        handlers={
            TaskType.FETCH: lambda *_: (_ for _ in ()).throw(
                AcquisitionFailureError(failure)
            )
        },
    )

    circuit = RuntimeCircuitRepository(tmp_path).get("platform", "x")
    assert circuit["state"] == "open"
    assert circuit["recovery_mode"] == "manual"
    assert circuit["cooldown_until"] is None
    assert circuit["user_action"] == "login_or_config_required"
    assert "cookie secret" not in str(circuit)
    assert "cookie secret" not in str(repo.get("auth-fails").error_reason)


def test_lost_terminal_cas_leaves_no_manual_circuit_incident_or_side_effect(
    tmp_path, monkeypatch
) -> None:
    _source(tmp_path, "x-lost", "x")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("auth-lost", TaskType.FETCH, "source", "x-lost"))
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            """
            update tasks set status = 'running', claimed_by = 'actual-owner'
            where task_id = 'auth-lost'
            """
        )
    setup_path = tmp_path / "setup-state.json"
    sends: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: setup_path,
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.enqueue_notification",
        lambda *args, **kwargs: sends.append(kwargs["related"][1]) or ["synthetic"],
    )
    failure = AcquisitionFailure(
        reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
        message="login required",
        retryable=False,
        user_action="login",
        diagnostic={},
    )
    persist_task_failure(
        repo,
        repo.get("auth-lost"),
        worker_id="lost-owner",
        now="2026-07-26T02:00:00Z",
        error=AcquisitionFailureError(failure),
        artifacts={},
        base_path=tmp_path,
        platform_scope="x",
    )

    assert RuntimeCircuitRepository(tmp_path).get("platform", "x") is None
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute(
            "select count(*) from auth_recovery_incidents"
        ).fetchone()[0] == 0
    assert not setup_path.exists()
    assert sends == []


def test_lost_retry_cas_does_not_record_automatic_circuit(tmp_path) -> None:
    _source(tmp_path, "x-retry-lost", "x")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("retry-lost", TaskType.FETCH, "source", "x-retry-lost"))
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            """
            update tasks set status = 'running', claimed_by = 'actual-owner'
            where task_id = 'retry-lost'
            """
        )
    failure = AcquisitionFailure(
        reason=FetchFailureReason.NETWORK_TIMEOUT,
        message="timeout",
        retryable=True,
        user_action=None,
        diagnostic={},
    )
    persist_task_failure(
        repo,
        repo.get("retry-lost"),
        worker_id="lost-owner",
        now="2026-07-26T02:00:00Z",
        error=AcquisitionFailureError(failure),
        artifacts={},
        base_path=tmp_path,
        platform_scope="x",
    )

    assert RuntimeCircuitRepository(tmp_path).get("platform", "x") is None


def test_manual_platform_failure_callback_rejects_missing_scope() -> None:
    with pytest.raises(ValueError, match="requires a platform scope"):
        platform_circuits.manual_platform_failure_callback(
            {},
            Task("missing-scope", TaskType.FETCH, "source", "source-one"),
            platform_scope=None,
            reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
            occurred_at="2026-07-26T02:00:00Z",
        )


@pytest.mark.parametrize(
    ("task_type", "platform_scope"),
    ((TaskType.CARD_BUILD, "x"), (TaskType.FETCH, None)),
)
def test_auth_failure_without_fetch_platform_scope_creates_no_recovery_state(
    tmp_path, task_type, platform_scope
) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    task_id = f"no-scope-{task_type.value}"
    repo.enqueue(Task(task_id, task_type, "source", "source-one"))
    with sqlite3.connect(repo.db_path) as conn:
        conn.execute(
            "update tasks set status = 'running', claimed_by = 'actual-owner' where task_id = ?",
            (task_id,),
        )
    failure = AcquisitionFailure(
        reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
        message="login required",
        retryable=False,
        user_action="login",
        diagnostic={},
    )

    persist_task_failure(
        repo,
        repo.get(task_id),
        worker_id="actual-owner",
        now="2026-07-26T02:00:00Z",
        error=AcquisitionFailureError(failure),
        artifacts={},
        base_path=tmp_path,
        platform_scope=platform_scope,
    )

    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("select count(*) from runtime_circuits").fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from auth_recovery_incidents"
        ).fetchone()[0] == 0
