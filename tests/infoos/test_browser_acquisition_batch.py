from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from backend.application.tasks.types import Task, TaskType
from backend.application.workflows.browser_acquisition_batch import bind_browser_acquisition_batch, browser_acquisition_batch
from backend.infrastructure.opencli.tab_lease import opencli_tab_lease
from backend.infrastructure.storage.task_events import TaskEventRepository


def _browser_task(source_id: str) -> Task:
    return Task(
        task_id=f"fetch_{source_id}",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source_id,
    )


def test_browser_batch_prepares_once_for_multiple_sources(tmp_path: Path, monkeypatch):
    prepares: list[dict] = []
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: prepares.append(kwargs) or {"status": "ready", "browser_headless": False},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )

    with browser_acquisition_batch(tmp_path, tasks=[_browser_task("x_a"), _browser_task("x_b")]) as state:
        assert state.source_ids == ["x_a", "x_b"]

    assert len(prepares) == 1


def test_browser_batch_cleans_up_once_after_all_sources(tmp_path: Path, monkeypatch):
    cleanups: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": True},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: cleanups.append("cleanup"),
    )

    with browser_acquisition_batch(tmp_path, tasks=[_browser_task("x_a"), _browser_task("x_b")]):
        pass

    assert cleanups == ["cleanup"]


def test_browser_batch_cleans_up_when_one_source_raises(tmp_path: Path, monkeypatch):
    cleanups: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": True},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: cleanups.append("cleanup"),
    )

    with pytest.raises(RuntimeError, match="source failed"):
        with browser_acquisition_batch(tmp_path, tasks=[_browser_task("x_a")]):
            raise RuntimeError("source failed")

    assert cleanups == ["cleanup"]


def test_repair_success_does_not_erase_headless_cleanup_requirement(tmp_path: Path, monkeypatch):
    cleanups: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": True},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: cleanups.append("cleanup"),
    )

    with browser_acquisition_batch(tmp_path, tasks=[_browser_task("x_a")]) as state:
        state.record_repair(
            original_diagnostic={"category": "browser_session", "summary": "extension disconnected"},
            repair_result={"status": "ready", "browser_headless": False},
            retried_at="2026-07-10T08:00:00Z",
        )
        assert state.should_cleanup_headless is True

    assert cleanups == ["cleanup"]


def test_browser_disconnect_records_original_diagnostic_and_repair(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": False},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )

    with browser_acquisition_batch(tmp_path, tasks=[_browser_task("x_a")]) as state:
        state.record_repair(
            original_diagnostic={"category": "browser_session", "summary": "extension disconnected"},
            repair_result={"status": "ready", "browser_headless": False},
            retried_at="2026-07-10T08:00:00Z",
        )

    assert state.repair_attempts == [
        {
            "attempt": 1,
            "original_diagnostic": {"category": "browser_session", "summary": "extension disconnected"},
            "repair_result": {"status": "ready", "browser_headless": False},
            "retried_at": "2026-07-10T08:00:00Z",
        }
    ]


def test_browser_batch_repair_prepares_once_and_returns_auditable_record(tmp_path: Path, monkeypatch):
    prepared: list[dict] = []
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: prepared.append(kwargs) or {"status": "ready", "browser_headless": False},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )
    from backend.application.workflows.browser_acquisition_batch import repair_browser_acquisition_batch

    with browser_acquisition_batch(tmp_path, tasks=[_browser_task("x_a")]) as state:
        repair = repair_browser_acquisition_batch(
            state,
            original_diagnostic={"category": "browser_session", "summary": "extension disconnected"},
            retried_at="2026-07-10T08:00:00Z",
        )

    assert repair["status"] == "ready"
    assert len(prepared) == 2
    assert state.repair_attempts[0]["original_diagnostic"]["summary"] == "extension disconnected"


def test_concurrent_batch_failures_elect_one_repair_leader(tmp_path: Path, monkeypatch):
    from backend.application.workflows.browser_acquisition_batch import (
        BrowserBatchState,
        repair_browser_acquisition_batch,
    )

    calls: list[str] = []

    def prepare(**_kwargs):
        calls.append("repair")
        time.sleep(0.08)
        return {"status": "ready", "browser_headless": False}

    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        prepare,
    )
    tasks = [_browser_task("x_a"), _browser_task("x_b")]
    state = BrowserBatchState(tmp_path, tasks, {"status": "ready"}, False, ["x_a", "x_b"])
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def repair(source_id: str) -> None:
        barrier.wait()
        results.append(repair_browser_acquisition_batch(
            state,
            original_diagnostic={"category": "browser_session", "source_id": source_id},
            retried_at="2026-07-15T00:00:00Z",
        ))

    threads = [threading.Thread(target=repair, args=(source_id,)) for source_id in ("x_a", "x_b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert calls == ["repair"]
    assert results == [
        {"status": "ready", "browser_headless": False},
        {"status": "ready", "browser_headless": False},
    ]
    assert len(state.repair_attempts) == 1


def test_browser_batch_records_prepare_repair_and_cleanup_events(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": True},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: None,
    )
    from backend.application.workflows.browser_acquisition_batch import repair_browser_acquisition_batch

    task = _browser_task("x_a")
    task = Task(
        **{**task.__dict__, "artifacts_summary": {"run_id": "run-browser", "chain_id": "chain-browser"}}
    )
    with browser_acquisition_batch(tmp_path, tasks=[task]) as state:
        repair_browser_acquisition_batch(
            state,
            original_diagnostic={"category": "browser_session"},
            retried_at="2026-07-10T08:00:00Z",
        )

    events = TaskEventRepository(tmp_path).list_events(chain_id="chain-browser")
    assert [event["event_type"] for event in events] == [
        "browser_prepare_started",
        "browser_prepare_completed",
        "browser_repair_started",
        "browser_repair_completed",
        "browser_cleanup_completed",
    ]
    assert all(event["payload"]["source_ids"] == ["x_a"] for event in events)
    assert all(event["payload"]["run_id"] == "run-browser" for event in events)


def test_browser_batch_binds_compact_tab_lease_events_to_current_task(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": False},
    )
    task = _browser_task("x_a")
    with browser_acquisition_batch(tmp_path, tasks=[task]) as state:
        with bind_browser_acquisition_batch(state, task=task):
            with opencli_tab_lease("twitter"):
                pass

    events = TaskEventRepository(tmp_path).list_events(task_id=task.task_id)
    lease_events = [event for event in events if event["event_type"].startswith("opencli_tab_")]
    assert [event["event_type"] for event in lease_events] == [
        "opencli_tab_stale_close_completed",
        "opencli_tab_lease_acquired",
        "opencli_tab_close_completed",
    ]
    assert all("stdout" not in event["payload"] and "command" not in event["payload"] for event in lease_events)


def test_browser_cleanup_failure_is_recorded_without_replacing_business_success(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": True},
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )
    task = _browser_task("x_a")

    with browser_acquisition_batch(tmp_path, tasks=[task]):
        business_result = "success"

    assert business_result == "success"
    event = TaskEventRepository(tmp_path).list_events(task_id=task.task_id)[-1]
    assert event["event_type"] == "browser_cleanup_failed"
    assert event["payload"]["error_class"] == "RuntimeError"
