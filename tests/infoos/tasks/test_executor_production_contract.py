"""Minimal admission contract shared by continuous and synchronous execution."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from backend.application.tasks.continuous_executor import run_continuous_tasks, drain_once
from backend.application.tasks.execution_clock import add_seconds, current_iso
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.runtime.resource_pressure import RuntimePressureSnapshot
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository


def _source(base: Path, source_id: str, platform: str) -> None:
    SourceRepository(base).upsert_source({
        "source_id": source_id, "platform": platform, "source_type": "creator",
        "identifier": source_id, "display_name": source_id, "cadence_hours": 24,
        "enabled": True, "connector_status": "supported",
        "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
    })


def _run(base, queue, stop, handlers, *, mode, limit=50):
    if mode == "synchronous":
        return drain_once(base, queue_names=[queue], limit=limit, handlers=handlers, max_workers=2)["processed"]
    timeout = threading.Timer(10, stop.set)
    timeout.start()
    try:
        return run_continuous_tasks(
            base, queue_name=queue, stop_event=stop, handlers=handlers,
            batch_limit=limit, poll_seconds=0.01, max_workers=2,
        )
    finally:
        timeout.cancel()


@pytest.mark.parametrize("mode", ["continuous", "synchronous"])
def test_open_circuit_returns_task_without_handler_or_attempt(tmp_path, monkeypatch, mode):
    _source(tmp_path, "rss", "rss")
    _source(tmp_path, "github", "github")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([Task(name, TaskType.FETCH, "source", name) for name in ("rss", "github")])
    now = current_iso()
    RuntimeCircuitRepository(tmp_path).record_failure(
        "platform", "rss", occurred_at=now,
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=60),
    )
    deferred, stop = threading.Event(), threading.Event()
    original = TaskRepository.defer_owned_for_circuit

    def observe(self, *args, **kwargs):
        returned = original(self, *args, **kwargs)
        deferred.set()
        return returned

    monkeypatch.setattr(TaskRepository, "defer_owned_for_circuit", observe)
    calls = []

    def handle(task, _base):
        calls.append(task.task_id)
        assert deferred.wait(5)
        stop.set()
        return {"status": "success", "artifacts": {}}

    assert _run(tmp_path, "public_fetch", stop, {TaskType.FETCH: handle}, mode=mode) == 1
    blocked = repo.get("rss")
    assert calls == ["github"]
    assert blocked.status is TaskStatus.PENDING and blocked.claimed_by is None
    assert blocked.attempt == 1 and blocked.next_run_at == add_seconds(now, 60)


@pytest.mark.parametrize("mode", ["continuous", "synchronous"])
def test_pressure_leaves_media_pending_while_light_task_executes(tmp_path, monkeypatch, mode):
    from backend.application.tasks import continuous_executor, resource_governor

    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        Task("media", TaskType.VIDEO_EXTRACT_AUDIO, "asset", "media"),
        Task("light", TaskType.MEDIA_DOWNLOAD, "card", "light"),
    ])
    pressure_seen, stop = threading.Event(), threading.Event()
    monkeypatch.setattr(resource_governor, "collect_runtime_pressure", lambda: RuntimePressureSnapshot(
        memory_full_avg10=4.5, mem_available_bytes=20 * 1024**3,
        cgroup_current_bytes=7 * 1024**3,
    ))
    original = resource_governor.resource_plan_for

    def observe(task):
        plan = original(task)
        if not plan.allowed:
            pressure_seen.set()
        return plan

    monkeypatch.setattr(continuous_executor, "resource_plan_for", observe)
    calls = []

    def handle(task, _base):
        calls.append(task.task_id)
        assert pressure_seen.wait(5)
        stop.set()
        return {"status": "success", "artifacts": {}}

    assert _run(tmp_path, "urgent_media", stop, {
        TaskType.VIDEO_EXTRACT_AUDIO: handle, TaskType.MEDIA_DOWNLOAD: handle,
    }, mode=mode) == 1
    media = repo.get("media")
    assert calls == ["light"]
    assert media.status is TaskStatus.PENDING and media.claimed_by is None
    assert media.attempt == 1


@pytest.mark.parametrize("mode", ["continuous", "synchronous"])
def test_browser_backlog_keeps_other_site_runnable(tmp_path, monkeypatch, mode):
    from backend.application.workflows import browser_acquisition_batch as batch

    _source(tmp_path, "x", "x")
    _source(tmp_path, "youtube", "youtube")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        *(Task(f"x-{i:03d}", TaskType.FETCH, "source", "x") for i in range(70)),
        Task("youtube", TaskType.FETCH, "source", "youtube"),
    ])
    monkeypatch.setattr(batch, "prepare_opencli_browser_session", lambda **_: {
        "status": "ready", "browser_headless": False,
    })
    first, other, stop = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def handle(task, _base):
        calls.append(task.task_id)
        if task.task_id == "x-000":
            first.set()
            assert other.wait(5)
        elif task.task_id == "youtube":
            assert first.wait(5)
            stop.set()
            other.set()
        else:
            raise AssertionError("same site attempted again while inflight")
        return {"status": "success", "artifacts": {}}

    assert _run(tmp_path, "browser_fetch", stop, {TaskType.FETCH: handle}, mode=mode, limit=2) == 2
    assert set(calls) == {"x-000", "youtube"}
    assert repo.get("x-001").status is TaskStatus.PENDING
