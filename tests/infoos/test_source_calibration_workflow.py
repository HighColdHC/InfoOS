from __future__ import annotations

from pathlib import Path

import pytest

from tests.infoos._factories import card_count
from backend.application.tasks.lanes import ExecutionLane, execution_lane_for
from backend.application.tasks.planner import enqueue_source_calibration_task
from backend.application.tasks.registry import handler_for
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.application.workflows.acquisition_failures import AcquisitionFailureError
from backend.infrastructure.storage.repositories import RawItemRepository, SourceCalibrationRepository, SourceRepository


def _source(*, source_id: str = "rss_calibration", platform: str = "rss") -> dict:
    return {
        "source_id": source_id,
        "platform": platform,
        "source_type": "feed" if platform == "rss" else "creator",
        "identifier": "https://example.com/feed.xml" if platform == "rss" else "openai",
        "display_name": source_id,
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": source_id,
    }


def _seed_task(base_dir: Path, *, source: dict | None = None):
    source = source or _source()
    SourceRepository(base_dir).upsert_source(source)
    task_id = enqueue_source_calibration_task(
        base_dir,
        source,
        now="2026-07-11T08:00:00Z",
    )
    assert task_id is not None
    return TaskRepository.for_base_dir(base_dir).get(task_id)


def test_successful_calibration_promotes_source_and_enqueues_first_fetch(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 3, "sample_count": 1, "samples": [{"title": "item"}]},
    )

    result = handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    assert result["status"] == TaskStatus.SUCCESS.value
    assert SourceRepository(tmp_path).get_source(task.target_id)["connector_status"] == "supported"
    evidence = SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)
    assert evidence["result"] == "success"
    assert evidence["visible_count"] == 3
    assert evidence["raw_payload"] == {"probe": {"visible_count": 3, "sample_count": 1}}
    fetch_ids = result["artifacts"]["fetch_task_ids"]
    assert len(fetch_ids) == 1
    fetch = TaskRepository.for_base_dir(tmp_path).get(fetch_ids[0])
    assert fetch.task_type is TaskType.FETCH
    assert fetch.depends_on[0].task_id == task.task_id
    assert fetch.artifacts_summary["chain_id"] == task.artifacts_summary["chain_id"]
    assert RawItemRepository(tmp_path).list_raw_items() == []
    assert card_count(tmp_path) == 0


def test_material_edit_calibration_enqueues_current_fetch_despite_old_active_version(tmp_path, monkeypatch):
    from backend.application.tasks.planner import plan_fetch_tasks_for_source

    old_source = _source()
    old_source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(old_source)
    stored_old = SourceRepository(tmp_path).get_source(old_source["source_id"])
    old_fetch = plan_fetch_tasks_for_source(
        tmp_path,
        stored_old,
        now="2026-07-11T08:00:00Z",
        horizon_hours=0,
    )[0]
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(old_fetch)

    edited = {**_source(), "identifier": "https://example.com/edited.xml"}
    task = _seed_task(tmp_path, source=edited)
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 1, "sample_count": 1, "samples": []},
    )

    result = handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    new_fetch = repo.get(result["artifacts"]["fetch_task_ids"][0])
    assert new_fetch.input_version == task.input_version
    assert new_fetch.task_id != old_fetch.task_id
    assert repo.get(old_fetch.task_id).status == TaskStatus.SKIPPED
    assert repo.get(old_fetch.task_id).error_reason == "source_configuration_changed"


def test_empty_calibration_stays_uncalibrated_and_blocks_without_fetch(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 0, "sample_count": 0, "samples": []},
    )

    result = handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    assert result["status"] == TaskStatus.BLOCKED.value
    assert result["error"] == "calibration_returned_no_items"
    assert SourceRepository(tmp_path).get_source(task.target_id)["connector_status"] == "implemented_uncalibrated"
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)["failure_class"] == "empty_calibration"
    assert TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source", target_id=task.target_id, task_type=TaskType.FETCH
    ) == []


@pytest.mark.parametrize("message,failure_class", [
    ("AUTH_REQUIRED: platform not logged in", "platform_not_logged_in"),
    ("X account @openai not found or unavailable", "source_unavailable"),
])
def test_non_retryable_failure_records_evidence_then_raises(tmp_path, monkeypatch, message, failure_class):
    task = _seed_task(tmp_path, source=_source(source_id="x_calibration", platform="x"))

    def fail_probe(*_args, **_kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        fail_probe,
    )

    with pytest.raises(AcquisitionFailureError) as captured:
        handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    assert captured.value.failure.retryable is False
    evidence = SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)
    assert evidence["result"] == "failed"
    assert evidence["failure_class"] == failure_class


def test_retryable_network_failure_does_not_freeze_failed_evidence(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)

    def fail_probe(*_args, **_kwargs):
        raise TimeoutError("network timeout")

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        fail_probe,
    )

    with pytest.raises(AcquisitionFailureError) as captured:
        handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    assert captured.value.failure.retryable is True
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id) is None


def test_calibration_replay_reuses_evidence_and_fetch_task(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)
    calls = 0

    def probe(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {"visible_count": 1, "sample_count": 1, "samples": []}

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        probe,
    )

    first = handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)
    second = handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    assert first["artifacts"]["fetch_task_ids"] == second["artifacts"]["fetch_task_ids"]
    assert calls == 1


def test_worker_consumes_successful_calibration_from_due_queue(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 1, "sample_count": 1, "samples": []},
    )

    summary = run_due_tasks(
        tmp_path,
        now="2026-07-11T08:00:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )

    assert summary["processed"] == 1
    assert summary["succeeded"] == 1
    assert TaskRepository.for_base_dir(tmp_path).get(task.task_id).status is TaskStatus.SUCCESS


def test_worker_blocks_auth_failure_and_requeues_network_failure(tmp_path, monkeypatch):
    auth_task = _seed_task(tmp_path)

    def auth_failure(*_args, **_kwargs):
        raise RuntimeError("AUTH_REQUIRED: platform not logged in")

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        auth_failure,
    )
    run_due_tasks(
        tmp_path,
        now="2026-07-11T08:00:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )
    persisted_auth = TaskRepository.for_base_dir(tmp_path).get(auth_task.task_id)
    assert persisted_auth.status is TaskStatus.BLOCKED
    assert persisted_auth.last_failure_class == "platform_not_logged_in"

    network_source = _source(source_id="rss_network")
    network_task = _seed_task(tmp_path, source=network_source)

    def network_failure(*_args, **_kwargs):
        raise TimeoutError("network timeout")

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        network_failure,
    )
    summary = run_due_tasks(
        tmp_path,
        now="2026-07-11T08:00:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )
    persisted_network = TaskRepository.for_base_dir(tmp_path).get(network_task.task_id)
    assert summary["requeued"] == 1
    assert persisted_network.status is TaskStatus.PENDING
    assert persisted_network.last_failure_class == "network_timeout"
    assert persisted_network.next_run_at > "2026-07-11T08:00:00Z"


def test_auth_retry_reprobes_with_new_attempt_and_can_promote(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)

    def auth_failure(*_args, **_kwargs):
        raise RuntimeError("AUTH_REQUIRED: platform not logged in")

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        auth_failure,
    )
    run_due_tasks(
        tmp_path,
        now="2026-07-11T08:00:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )
    assert TaskRepository.for_base_dir(tmp_path).retry(task.task_id) is True
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 1, "sample_count": 1, "samples": []},
    )

    summary = run_due_tasks(
        tmp_path,
        now="2026-07-11T08:01:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )

    assert summary["succeeded"] == 1
    assert SourceRepository(tmp_path).get_source(task.target_id)["connector_status"] == "supported"
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)["result"] == "success"


def test_stale_calibration_cannot_promote_changed_source(tmp_path, monkeypatch):
    from backend.application.tasks.input_versions import source_calibration_input_version

    task = _seed_task(tmp_path)

    def probe_then_edit(base_dir, source, **_kwargs):
        changed = {**source, "identifier": "https://example.com/changed.xml", "connector_status": "implemented_uncalibrated"}
        SourceRepository(base_dir).upsert_source(changed)
        return {"visible_count": 1, "sample_count": 1, "samples": []}

    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        probe_then_edit,
    )

    result = handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)
    changed = SourceRepository(tmp_path).get_source(task.target_id)

    assert result["status"] == TaskStatus.SKIPPED.value
    assert result["error"] == "source_configuration_changed"
    assert changed["connector_status"] == "implemented_uncalibrated"
    assert source_calibration_input_version(changed) != task.input_version
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id) is None


def test_fetch_enqueue_failure_rolls_back_calibration_promotion(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 1, "sample_count": 1, "samples": []},
    )

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("forced fetch enqueue failure")

    monkeypatch.setattr(
        TaskRepository,
        "enqueue_periodic_fetch_singleflight",
        fail_enqueue,
    )

    with pytest.raises(RuntimeError, match="forced fetch enqueue failure"):
        handler_for(TaskType.SOURCE_CALIBRATE)(task, tmp_path)

    assert SourceRepository(tmp_path).get_source(task.target_id)["connector_status"] == "implemented_uncalibrated"
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id) is None


def test_empty_calibration_manual_retry_reprobes(tmp_path, monkeypatch):
    task = _seed_task(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 0, "sample_count": 0, "samples": []},
    )
    run_due_tasks(
        tmp_path,
        now="2026-07-11T08:00:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )
    assert TaskRepository.for_base_dir(tmp_path).retry(task.task_id) is True
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: {"visible_count": 1, "sample_count": 1, "samples": []},
    )

    summary = run_due_tasks(
        tmp_path,
        now="2026-07-11T08:01:00Z",
        task_types=[TaskType.SOURCE_CALIBRATE],
        max_workers=1,
    )

    assert summary["succeeded"] == 1
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)["attempt"] == 2


def test_fetch_refuses_source_that_was_downgraded_after_calibration(tmp_path, monkeypatch):
    from backend.application.workflows.acquisition import fetch_source

    source = _source()
    SourceRepository(tmp_path).upsert_source(source)
    fetch = Task(
        task_id="fetch_stale_calibration",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source["source_id"],
        artifacts_summary={"enforce_source_capability": True},
    )
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda *_args: (_ for _ in ()).throw(AssertionError("connector must not run")),
    )

    with pytest.raises(ValueError, match="disabled or unsupported"):
        fetch_source(tmp_path, source_id=source["source_id"], task=fetch)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (_source(), ExecutionLane.PUBLIC_ACQUISITION),
        (_source(source_id="x_calibration", platform="x"), ExecutionLane.BROWSER_ACQUISITION),
    ],
)
def test_calibration_uses_source_acquisition_lane(tmp_path, source, expected):
    task = _seed_task(tmp_path, source=source)

    assert execution_lane_for(task, tmp_path) is expected
