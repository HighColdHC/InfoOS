from __future__ import annotations

from pathlib import Path
import threading

import pytest

import backend.application.tasks.planner as planner_module
from backend.application.tasks.planner import (
    enqueue_fetch_tasks_for_source,
    enqueue_fetch_tasks_for_enabled_sources,
    enqueue_pipeline_reconciliation_task,
    enqueue_task_event_archive_task,
    enqueue_source_calibration_task,
    plan_initial_fetch,
    versioned_task_id,
)
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.infrastructure.storage.repositories import SourceRepository


def test_versioned_task_id_contains_short_hash() -> None:
    input_version = "0123456789abcdef" * 4

    assert versioned_task_id(TaskType.SUMMARY, "card-1", input_version) == "summary_card-1_0123456789ab"


def test_pipeline_reconciliation_policy_change_requeues_same_hour(tmp_path: Path, monkeypatch) -> None:
    first = enqueue_pipeline_reconciliation_task(tmp_path, now="2026-07-20T06:10:00Z")

    monkeypatch.setattr(
        planner_module,
        "PIPELINE_RECONCILIATION_POLICY_VERSION",
        "pipeline-reconcile-v-next",
    )
    changed = enqueue_pipeline_reconciliation_task(tmp_path, now="2026-07-20T06:20:00Z")

    assert first != changed
    assert TaskRepository.for_base_dir(tmp_path).get(changed).artifacts_summary["policy_version"] == (
        "pipeline-reconcile-v-next"
    )


def test_task_event_archive_plans_one_task_per_utc_hour(tmp_path: Path) -> None:
    first = enqueue_task_event_archive_task(tmp_path, now="2026-07-20T06:10:00Z")
    repeated = enqueue_task_event_archive_task(tmp_path, now="2026-07-20T06:50:00Z")
    next_hour = enqueue_task_event_archive_task(tmp_path, now="2026-07-20T07:00:00Z")
    assert first == repeated
    assert first != next_hour
    assert TaskRepository.for_base_dir(tmp_path).get(first).queue_name == "maintenance_io"


def test_calibration_planner_enqueues_one_versioned_source_task(tmp_path: Path):
    source = {
        "source_id": "rss_calibrate",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_calibrate",
        "raw_metadata": {},
    }

    first = enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T06:00:00Z")
    second = enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T06:01:00Z")

    assert first == second
    task = TaskRepository.for_base_dir(tmp_path).get(first)
    assert task.task_type == TaskType.SOURCE_CALIBRATE
    assert task.target_kind == "source"
    assert task.target_id == "rss_calibrate"
    assert task.artifacts_summary["chain_id"].startswith("source_calibration_rss_calibrate_")


def test_initial_fetch_plan_is_deterministic_and_versioned() -> None:
    source = {
        "source_id": "rss_initial",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "enabled": True,
        "connector_status": "implemented_uncalibrated",
        "connector_id": "rss_initial",
        "raw_metadata": {},
    }

    first = plan_initial_fetch(source, now="2026-07-13T00:00:00Z")
    same = plan_initial_fetch(source, now="2026-07-13T00:01:00Z")
    changed = plan_initial_fetch({**source, "identifier": "https://example.com/new.xml"})

    assert first is not None and same is not None and changed is not None
    assert first.task_id == same.task_id
    assert first.input_version == first.artifacts_summary["source_input_version"]
    assert first.artifacts_summary["initial_fetch"] is True
    assert first.artifacts_summary["chain_root_task_id"] == first.task_id
    assert first.task_id != changed.task_id


def test_all_fetch_plans_carry_source_fingerprint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_versioned",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "connector_id": "rss_versioned",
        "raw_metadata": {},
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }

    scheduled = planner_module.plan_fetch_tasks_for_source(
        tmp_path, source, now="2026-07-13T00:00:00Z", horizon_hours=0
    )[0]
    manual = planner_module.plan_manual_fetch_task_for_source(
        source, now="2026-07-13T00:00:00Z", manual_run_id="manual"
    )

    assert manual is not None
    for task in (scheduled, manual):
        assert task.input_version != "v1"
        assert task.artifacts_summary["source_input_version"] == task.input_version


def test_fetch_dedup_only_reuses_same_source_version(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_version_dedup",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/old.xml",
        "connector_id": "rss_version_dedup",
        "raw_metadata": {},
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }
    old_ids = enqueue_fetch_tasks_for_source(
        tmp_path, source, now="2026-07-13T00:00:00Z", horizon_hours=0
    )
    changed = {**source, "identifier": "https://example.com/new.xml"}
    new_ids = enqueue_fetch_tasks_for_source(
        tmp_path, changed, now="2026-07-13T00:00:00Z", horizon_hours=0
    )

    assert len(old_ids) == len(new_ids) == 1
    assert old_ids[0] != new_ids[0]
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source", target_id="rss_version_dedup", task_type=TaskType.FETCH
    )
    assert len(tasks) == 2
    assert len({task.input_version for task in tasks}) == 2


@pytest.mark.parametrize(
    "enabled,connector_status",
    [(False, "implemented_uncalibrated"), (True, "unsupported"), (True, "supported")],
)
def test_calibration_planner_skips_sources_that_do_not_need_calibration(
    tmp_path: Path,
    enabled: bool,
    connector_status: str,
):
    source = {
        "source_id": "source_skip",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "enabled": enabled,
        "connector_status": connector_status,
    }

    assert enqueue_source_calibration_task(tmp_path, source, now="2026-07-11T06:00:00Z") is None


def test_fetch_planner_skips_existing_nearby_pending_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "x_user_elonmusk",
        "platform": "x",
        "source_type": "creator",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }

    first = enqueue_fetch_tasks_for_source(tmp_path, source, now="2026-06-23T05:36:15Z")
    second = enqueue_fetch_tasks_for_source(tmp_path, source, now="2026-06-23T05:36:35Z")

    assert len(first) == 1
    assert second == []
    tasks = TaskRepository.for_base_dir(tmp_path).list_tasks(task_type=TaskType.FETCH, limit=20)
    assert len(tasks) == 1


@pytest.mark.parametrize("terminal_status", [TaskStatus.SUCCESS, TaskStatus.FAILED])
def test_fetch_planner_keeps_one_active_run_across_scheduled_times(
    tmp_path: Path,
    monkeypatch,
    terminal_status: TaskStatus,
):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "x_user_elonmusk",
        "platform": "x",
        "source_type": "creator",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }

    first = enqueue_fetch_tasks_for_source(
        tmp_path, source, now="2026-06-23T05:00:00Z", horizon_hours=72
    )
    second = enqueue_fetch_tasks_for_source(
        tmp_path, source, now="2026-06-23T18:00:00Z", horizon_hours=72
    )

    assert len(first) == 1
    assert second == []
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.update_status(first[0], terminal_status)

    after_terminal = enqueue_fetch_tasks_for_source(
        tmp_path, source, now="2026-06-24T18:00:00Z", horizon_hours=72
    )

    assert len(after_terminal) == 1


def test_fetch_planner_treats_initial_fetch_as_active_single_flight(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_initial_active",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
        "raw_metadata": {},
    }
    initial = plan_initial_fetch(source, now="2026-06-23T05:00:00Z")
    assert initial is not None
    TaskRepository.for_base_dir(tmp_path).enqueue(initial)

    assert enqueue_fetch_tasks_for_source(
        tmp_path,
        source,
        now="2026-06-24T05:00:00Z",
    ) == []


def test_fetch_planner_does_not_merge_manual_run(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "x_manual_independent",
        "platform": "x",
        "source_type": "creator",
        "identifier": "manual_independent",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }
    manual = planner_module.plan_manual_fetch_task_for_source(
        source,
        now="2026-06-23T05:00:00Z",
        manual_run_id="manual-one",
    )
    assert manual is not None
    TaskRepository.for_base_dir(tmp_path).enqueue(manual)

    scheduled = enqueue_fetch_tasks_for_source(
        tmp_path,
        source,
        now="2026-06-23T06:00:00Z",
    )

    assert len(scheduled) == 1
    assert scheduled[0] != manual.task_id


def test_fetch_planner_atomically_enqueues_one_active_task_under_concurrency(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_atomic_singleflight",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/feed.xml",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
        "raw_metadata": {},
    }
    original = TaskRepository.list_by_target
    barrier = threading.Barrier(2)

    def synchronized_list(self, **kwargs):
        result = original(self, **kwargs)
        if kwargs.get("target_id") == "rss_atomic_singleflight":
            barrier.wait(timeout=2)
        return result

    monkeypatch.setattr(TaskRepository, "list_by_target", synchronized_list)
    created: list[list[str]] = []
    errors: list[Exception] = []

    def enqueue() -> None:
        try:
            created.append(enqueue_fetch_tasks_for_source(
                tmp_path,
                source,
                now="2026-08-12T00:00:00Z",
            ))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=enqueue) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sum(len(task_ids) for task_ids in created) == 1
    tasks = original(
        TaskRepository.for_base_dir(tmp_path),
        target_kind="source",
        target_id="rss_atomic_singleflight",
        task_type=TaskType.FETCH,
    )
    assert len(tasks) == 1


def test_scheduler_planner_repeated_entry_keeps_one_active_fetch(tmp_path: Path) -> None:
    SourceRepository(tmp_path).upsert_source({
        "source_id": "rss_scheduler_repeat",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/repeat.xml",
        "display_name": "Repeat",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
        "raw_metadata": {},
    })

    first = enqueue_fetch_tasks_for_enabled_sources(
        tmp_path,
        now="2026-08-12T00:00:00Z",
    )
    second = enqueue_fetch_tasks_for_enabled_sources(
        tmp_path,
        now="2026-08-13T00:00:00Z",
    )

    assert len(first) == 1
    assert second == []
    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source",
        target_id="rss_scheduler_repeat",
        task_type=TaskType.FETCH,
    )
    assert len(tasks) == 1


def test_scheduler_planner_concurrent_entry_atomically_keeps_one_fetch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    SourceRepository(tmp_path).upsert_source({
        "source_id": "rss_scheduler_concurrent",
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.com/concurrent.xml",
        "display_name": "Concurrent",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
        "raw_metadata": {},
    })
    original = TaskRepository.list_by_target
    barrier = threading.Barrier(2)

    def synchronized_list(self, **kwargs):
        result = original(self, **kwargs)
        if kwargs.get("target_id") == "rss_scheduler_concurrent":
            barrier.wait(timeout=2)
        return result

    monkeypatch.setattr(TaskRepository, "list_by_target", synchronized_list)
    created: list[list[str]] = []
    errors: list[Exception] = []

    def enqueue() -> None:
        try:
            created.append(enqueue_fetch_tasks_for_enabled_sources(
                tmp_path,
                now="2026-08-12T00:00:00Z",
            ))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=enqueue) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sum(len(task_ids) for task_ids in created) == 1
    tasks = original(
        TaskRepository.for_base_dir(tmp_path),
        target_kind="source",
        target_id="rss_scheduler_concurrent",
        task_type=TaskType.FETCH,
    )
    assert len(tasks) == 1


def test_fetch_planner_default_only_materializes_due_work(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_due_only",
        "platform": "rss",
        "source_type": "feed",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
        "next_run_at": "2026-06-24T05:00:00Z",
    }

    assert enqueue_fetch_tasks_for_source(
        tmp_path, source, now="2026-06-23T05:00:00Z"
    ) == []

    expanded = enqueue_fetch_tasks_for_source(
        tmp_path,
        source,
        now="2026-06-23T05:00:00Z",
        horizon_hours=48,
    )

    assert len(expanded) == 1


def test_fetch_planner_can_disable_followup_fetch_planning(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_manual",
        "platform": "rss",
        "source_type": "feed",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 0.5,
    }

    task_ids = enqueue_fetch_tasks_for_source(
        tmp_path,
        source,
        now="2026-06-23T05:36:15Z",
        horizon_hours=0,
        plan_followup_fetch=False,
    )

    assert len(task_ids) == 1
    assert task_ids[0].startswith("fetch_rss_manual_20260623_053615_")
    task = TaskRepository.for_base_dir(tmp_path).get(task_ids[0])
    assert task is not None
    assert task.artifacts_summary["plan_followup_fetch"] is False


def test_fetch_planner_marks_each_source_run_as_chain_root(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(planner_module, "source_can_enter_fetch_queue", lambda source: True)
    source = {
        "source_id": "rss_ai",
        "platform": "rss",
        "source_type": "feed",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }

    task_ids = enqueue_fetch_tasks_for_source(
        tmp_path,
        source,
        now="2026-06-23T05:36:15Z",
        horizon_hours=0,
    )

    task = TaskRepository.for_base_dir(tmp_path).get(task_ids[0])
    assert task.artifacts_summary["chain_id"] == "source_run_rss_ai_20260623_053615"
    assert task.artifacts_summary["chain_root_task_id"] == task_ids[0]


def test_planner_rejects_unsupported_capability_even_if_connector_status_says_supported(tmp_path: Path):
    source = {
        "source_id": "x_keyword",
        "platform": "x",
        "source_type": "keyword",
        "enabled": True,
        "connector_status": "supported",
        "cadence_hours": 24,
    }

    assert enqueue_fetch_tasks_for_source(tmp_path, source, now="2026-07-10T00:00:00Z") == []


@pytest.mark.parametrize("capability_status", ["supported", "partial"])
def test_planner_accepts_supported_or_partial_capability(tmp_path: Path, monkeypatch, capability_status: str):
    source = {
        "source_id": f"capability_{capability_status}",
        "platform": "rss",
        "source_type": "feed",
        "enabled": True,
        "connector_status": capability_status,
        "cadence_hours": 24,
    }
    monkeypatch.setattr(
        planner_module,
        "source_can_enter_fetch_queue",
        lambda candidate: candidate["connector_status"] == capability_status,
    )

    task_ids = enqueue_fetch_tasks_for_source(
        tmp_path,
        source,
        now="2026-07-10T00:00:00Z",
        horizon_hours=0,
    )

    assert len(task_ids) == 1
    assert task_ids[0].startswith(f"fetch_capability_{capability_status}_20260710_000000_")
