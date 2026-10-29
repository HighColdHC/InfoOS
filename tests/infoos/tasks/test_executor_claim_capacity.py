from __future__ import annotations

from pathlib import Path
import threading

import pytest

from backend.application.tasks.executor import run_due_tasks, run_tasks_by_ids
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import SourceRepository


def test_single_worker_does_not_claim_work_waiting_for_its_slot(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("occupies-slot", "recoverable-work"):
        repo.enqueue(Task(task_id, TaskType.CONTENT_ENRICH, "card", task_id))

    execution_order: list[str] = []

    def handler(task: Task, base_dir: Path) -> dict[str, object]:
        execution_order.append(task.task_id)
        if task.task_id == "occupies-slot":
            waiting = repo.get("recoverable-work")
            assert waiting is not None
            assert waiting.status == TaskStatus.PENDING
            assert waiting.claimed_by is None

            recovered = run_tasks_by_ids(
                base_dir,
                ["recoverable-work"],
                now="2099-01-01T00:00:00Z",
                max_workers=1,
                handlers={TaskType.CONTENT_ENRICH: handler},
            )
            assert recovered["succeeded"] == 1
        return {"status": "success", "artifacts": {}}

    result = run_due_tasks(
        tmp_path,
        now="2026-07-12T00:00:00Z",
        limit=2,
        max_workers=1,
        handlers={TaskType.CONTENT_ENRICH: handler},
    )

    assert result["succeeded"] == 1
    assert execution_order == ["occupies-slot", "recoverable-work"]
    first = repo.get("occupies-slot")
    second = repo.get("recoverable-work")
    assert first is not None and first.status == TaskStatus.SUCCESS
    assert second is not None and second.status == TaskStatus.SUCCESS


def test_run_due_tasks_does_not_consume_tasks_enqueued_during_execution(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("snapshot-task", TaskType.CONTENT_ENRICH, "card", "snapshot-card"))

    def handler(task: Task, _base_dir: Path) -> dict[str, object]:
        repo.enqueue(Task("new-downstream", TaskType.MEDIA_DOWNLOAD, "card", task.target_id))
        return {"status": "success", "artifacts": {}}

    result = run_due_tasks(
        tmp_path,
        limit=10,
        max_workers=1,
        handlers={TaskType.CONTENT_ENRICH: handler},
    )

    downstream = repo.get("new-downstream")
    assert result["processed"] == 1
    assert downstream is not None
    assert downstream.status == TaskStatus.PENDING
    assert downstream.claimed_by is None


def test_memory_pressure_defers_media_but_keeps_light_pipeline_moving(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.infrastructure.runtime.resource_pressure import (
        RuntimePressureSnapshot,
    )

    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("pressured-media", TaskType.VIDEO_EXTRACT_AUDIO, "asset", "asset-1"))
    repo.enqueue(Task("light-card", TaskType.CARD_FINALIZE, "card", "card-1"))
    monkeypatch.setattr(
        "backend.application.tasks.resource_governor.collect_runtime_pressure",
        lambda: RuntimePressureSnapshot(
            memory_full_avg10=4.5,
            mem_available_bytes=20 * 1024**3,
            cgroup_current_bytes=7 * 1024**3,
        ),
    )
    calls: list[str] = []

    result = run_due_tasks(
        tmp_path,
        limit=2,
        max_workers=2,
        handlers={
            TaskType.VIDEO_EXTRACT_AUDIO: lambda task, _base: calls.append(task.task_id)
            or {"status": "success", "artifacts": {}},
            TaskType.CARD_FINALIZE: lambda task, _base: calls.append(task.task_id)
            or {"status": "success", "artifacts": {}},
        },
    )

    media = repo.get("pressured-media")
    card = repo.get("light-card")
    assert result["resource_pressure_deferred"] == 1
    assert result["resource_pressure_reasons"] == {"memory_full_pressure": 1}
    assert calls == ["light-card"]
    assert media is not None and media.status is TaskStatus.PENDING
    assert card is not None and card.status is TaskStatus.SUCCESS


def test_each_capacity_batch_gets_a_fresh_lease(tmp_path: Path, monkeypatch) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("lease-one", "lease-two"):
        repo.enqueue(Task(task_id, TaskType.CONTENT_ENRICH, "card", task_id))
    times = iter(("2026-07-12T00:00:00Z", "2026-07-12T00:10:00Z"))
    monkeypatch.setattr("backend.application.tasks.continuous_executor.current_iso", lambda: next(times))
    leases: list[str] = []
    original = TaskRepository.claim_task_ids

    def observe(self, task_ids, **kwargs):
        leases.append(kwargs["lease_expires_at"])
        return original(self, task_ids, **kwargs)

    monkeypatch.setattr(TaskRepository, "claim_task_ids", observe)
    result = run_due_tasks(
        tmp_path, now="2026-07-12T00:00:00Z", limit=2, max_workers=1,
        handlers={TaskType.CONTENT_ENRICH: lambda *_args: {"status": "success", "artifacts": {}}},
    )

    assert result["succeeded"] == 2
    assert leases == ["2026-07-12T00:05:00Z", "2026-07-12T00:15:00Z"]


def test_task_type_generator_is_normalized_once(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("generated-filter", TaskType.CONTENT_ENRICH, "card", "card"))
    task_types = (task_type for task_type in (TaskType.CONTENT_ENRICH,))

    result = run_due_tasks(
        tmp_path, task_types=task_types,
        handlers={TaskType.CONTENT_ENRICH: lambda *_args: {"status": "success", "artifacts": {}}},
    )

    assert result["succeeded"] == 1


@pytest.mark.no_storage_bootstrap
def test_non_positive_limit_does_not_open_repository(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        TaskRepository, "for_base_dir",
        lambda *_args: (_ for _ in ()).throw(AssertionError("repository should not be opened")),
    )

    for limit in (0, -1):
        result = run_due_tasks(tmp_path, limit=limit)
        assert result["processed"] == 0
        assert result["recovered"] == 0


def test_unclaimable_browser_id_does_not_start_lifecycle(tmp_path: Path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source({
        "source_id": "x_done", "platform": "x", "source_type": "creator", "identifier": "done",
        "display_name": "done", "cadence_hours": 24, "enabled": True,
        "connector_status": "supported", "raw_metadata": {},
    })
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("browser-done", TaskType.FETCH, "source", "x_done", status=TaskStatus.SUCCESS))
    monkeypatch.setattr(
        "backend.application.tasks.continuous_browser.browser_acquisition_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("lifecycle should not start")),
    )

    assert run_tasks_by_ids(tmp_path, ["browser-done"])["processed"] == 0


def test_browser_prepare_is_heartbeat_protected_from_recovery(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as lifecycle_module

    SourceRepository(tmp_path).upsert_source({
        "source_id": "x_slow", "platform": "x", "source_type": "creator", "identifier": "slow",
        "display_name": "slow", "cadence_hours": 24, "enabled": True,
        "connector_status": "supported", "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
    })
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("slow-browser", TaskType.FETCH, "source", "x_slow"))
    prepare_started = threading.Event()
    release_prepare = threading.Event()
    heartbeat_seen = threading.Event()
    original_heartbeat = TaskRepository.heartbeat_with_resource

    def heartbeat(self, task_id, **kwargs):
        result = original_heartbeat(self, task_id, **kwargs)
        heartbeat_seen.set()
        return result

    def prepare(**_kwargs):
        prepare_started.set()
        assert release_prepare.wait(timeout=2)
        return {"status": "ready", "browser_headless": False}

    monkeypatch.setenv("INFOOS_TASK_LEASE_SECONDS", "2")
    monkeypatch.setattr("backend.application.tasks.continuous_executor.current_iso", lambda: "2026-07-12T00:00:00Z")
    monkeypatch.setattr(TaskRepository, "heartbeat_with_resource", heartbeat)
    monkeypatch.setattr("backend.application.tasks.processor_execution.current_iso", lambda: "2026-07-12T00:00:02Z")
    monkeypatch.setattr(lifecycle_module, "prepare_opencli_browser_session", prepare)
    calls: list[str] = []
    worker = threading.Thread(target=lambda: run_due_tasks(
        tmp_path, now="2026-07-12T00:00:00Z",
        handlers={TaskType.FETCH: lambda *_args: calls.append("first") or {"status": "success", "artifacts": {}}},
    ))
    worker.start()
    assert prepare_started.wait(timeout=1)
    assert heartbeat_seen.wait(timeout=1)

    competitor = run_due_tasks(
        tmp_path, now="2026-07-12T00:00:03Z",
        handlers={TaskType.FETCH: lambda *_args: calls.append("competitor") or {"status": "success", "artifacts": {}}},
    )
    release_prepare.set()
    worker.join(timeout=2)

    assert competitor["recovered"] == 0
    assert competitor["processed"] == 0
    assert calls == ["first"]
    persisted = repo.get("slow-browser")
    assert persisted is not None and persisted.status == TaskStatus.SUCCESS


def test_browser_startup_readiness_is_deferred_before_blocking(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as lifecycle_module

    SourceRepository(tmp_path).upsert_source({
        "source_id": "x_starting", "platform": "x", "source_type": "creator", "identifier": "starting",
        "display_name": "starting", "cadence_hours": 24, "enabled": True,
        "connector_status": "supported", "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
    })
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("browser-starting", TaskType.FETCH, "source", "x_starting"))
    monkeypatch.setattr(
        lifecycle_module,
        "prepare_opencli_browser_session",
        lambda **_kwargs: {
            "status": "blocked",
            "issues": ["Supervised Chrome or its OpenCLI extension is not ready"],
        },
    )

    first = run_due_tasks(
        tmp_path,
        now="2026-07-12T00:00:00Z",
        handlers={TaskType.FETCH: lambda *_args: pytest.fail("fetch must wait for browser readiness")},
    )

    persisted = repo.get("browser-starting")
    assert first["requeued"] == 1
    assert persisted is not None
    assert persisted.status == TaskStatus.PENDING
    assert persisted.next_run_at == "2026-07-12T00:00:31Z"
    assert persisted.artifacts_summary["browser_readiness_deferrals"] == 1
    assert persisted.artifacts_summary["browser_readiness_cached_deferrals"] == 0
    assert persisted.error_reason == "browser_session_not_ready"

    monkeypatch.setenv("INFOOS_BROWSER_PREPARE_FAILURE_COOLDOWN_SECONDS", "0")
    second = run_due_tasks(
        tmp_path,
        now="2026-07-12T00:00:31Z",
        handlers={TaskType.FETCH: lambda *_args: pytest.fail("fetch must wait for browser readiness")},
    )
    persisted = repo.get("browser-starting")
    assert second["requeued"] == 1
    assert persisted is not None
    assert persisted.status == TaskStatus.PENDING
    assert persisted.next_run_at == "2026-07-12T00:00:32Z"
    assert persisted.artifacts_summary["browser_readiness_deferrals"] == 2
    assert persisted.artifacts_summary["browser_readiness_cached_deferrals"] == 0

    third = run_due_tasks(
        tmp_path,
        now="2026-07-12T00:00:32Z",
        handlers={TaskType.FETCH: lambda *_args: pytest.fail("fetch must wait for browser readiness")},
    )
    persisted = repo.get("browser-starting")
    assert third["processed"] == 1
    assert persisted is not None
    assert persisted.status == TaskStatus.BLOCKED
    assert persisted.artifacts_summary["browser_readiness_deferrals"] == 3
    assert persisted.artifacts_summary["browser_readiness_cached_deferrals"] == 0


def test_browser_calibration_handler_runs_inside_shared_lifecycle(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as lifecycle_module
    from backend.application.workflows.browser_acquisition_batch import active_browser_acquisition_batch

    SourceRepository(tmp_path).upsert_source({
        "source_id": "x_calibrate", "platform": "x", "source_type": "creator", "identifier": "calibrate",
        "display_name": "calibrate", "cadence_hours": 24, "enabled": True,
        "connector_status": "supported", "raw_metadata": {},
    })
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("browser-calibration", TaskType.SOURCE_CALIBRATE, "source", "x_calibrate"))
    lifecycle_events: list[str] = []
    monkeypatch.setattr(
        lifecycle_module, "prepare_opencli_browser_session",
        lambda **_kwargs: lifecycle_events.append("prepare") or {"status": "ready", "browser_headless": True},
    )
    monkeypatch.setattr(
        lifecycle_module, "cleanup_headless_browser_session",
        lambda: lifecycle_events.append("cleanup"),
    )
    handler_calls: list[str] = []

    def calibrate(task: Task, _base_dir: Path) -> dict[str, object]:
        active = active_browser_acquisition_batch()
        assert active is not None
        assert active.tasks[0].task_id == task.task_id
        handler_calls.append(task.task_id)
        return {"status": "success", "artifacts": {}}

    result = run_due_tasks(
        tmp_path,
        handlers={TaskType.SOURCE_CALIBRATE: calibrate},
    )

    assert result["succeeded"] == 1
    assert handler_calls == ["browser-calibration"]
    assert lifecycle_events == ["prepare", "cleanup"]
