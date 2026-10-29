"""Browser lane behavior in the continuous worker."""
from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from backend.application.tasks.continuous_browser import BrowserLifetime
from backend.application.tasks.continuous_executor import run_continuous_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.browser_acquisition_batch import (
    active_browser_acquisition_batch, repair_browser_acquisition_batch,
)
from backend.infrastructure.storage.acquisition_checkpoints import AcquisitionCheckpointRepository
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.application.tasks.execution_clock import add_seconds, current_iso


def _source(base_path: Path, source_id: str, platform: str = "x") -> None:
    SourceRepository(base_path).upsert_source({
        "source_id": source_id, "platform": platform,
        "source_type": "creator", "identifier": source_id,
        "display_name": source_id, "cadence_hours": 24,
        "enabled": True, "connector_status": "supported",
        "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
    })


def _start(base_path: Path, stopping: threading.Event, handlers: dict,
           *, queue_name: str = "browser_fetch") -> tuple[threading.Thread, dict]:
    outcome: dict = {}

    def run() -> None:
        outcome["processed"] = run_continuous_tasks(
            base_path, queue_name=queue_name, stop_event=stopping,
            handlers=handlers, poll_seconds=0.01,
        )

    worker = threading.Thread(target=run)
    worker.start()
    return worker, outcome


def _seed_complete_replay(base_path: Path) -> TaskRepository:
    _source(base_path, "x_replay")
    task = Task(
        "replay", TaskType.FETCH, "source", "x_replay",
        artifacts_summary={"run_id": "replay-run"},
    )
    repo = TaskRepository.for_base_dir(base_path)
    repo.enqueue(task)
    checkpoint = {
        "source_id": "x_replay", "run_id": "replay-run",
        "source_input_version": "v1", "initial_fetch": False,
        "calibration_evidence_id": None,
        "committed_last_run_at": "2026-09-23T00:00:00Z",
        "committed_next_run_at": "2026-09-24T00:00:00Z",
        "raw_item_ids": [], "card_build_task_ids": [], "cursor_after": None,
    }
    with repo._connect() as conn:
        AcquisitionCheckpointRepository.upsert_to_connection(
            conn, task_id=task.task_id, source_id="x_replay",
            run_id="replay-run", source_input_version="v1",
            initial_fetch=False, calibration_evidence_id=None,
            committed_last_run_at=checkpoint["committed_last_run_at"],
            committed_next_run_at=checkpoint["committed_next_run_at"],
            payload=checkpoint,
        )
    return repo


def test_browser_slot_refill_reuses_one_batch_and_stops_after_handlers(
    tmp_path: Path, monkeypatch,
) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    for source_id, platform in (
        ("one", "x"), ("two", "youtube"), ("three", "xiaohongshu"),
    ):
        _source(tmp_path, source_id, platform)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        Task(source_id, TaskType.FETCH, "source", source_id)
        for source_id in ("one", "two", "three")
    ])
    prepares: list[str] = []
    cleanups: list[str] = []
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: prepares.append("prepare") or {
            "status": "ready", "browser_headless": True,
        },
    )
    monkeypatch.setattr(
        batch_module, "cleanup_headless_browser_session",
        lambda: cleanups.append("cleanup"),
    )
    first_started, second_started, third_started = (
        threading.Event(), threading.Event(), threading.Event()
    )
    release_first, release_second, stopping = (
        threading.Event(), threading.Event(), threading.Event()
    )
    states: list[object] = []

    def handle(task: Task, _base: Path) -> dict:
        state = active_browser_acquisition_batch()
        assert state is not None
        states.append(state)
        if task.task_id == "one":
            first_started.set()
            assert release_first.wait(3)
        elif task.task_id == "two":
            second_started.set()
            assert release_second.wait(3)
        else:
            third_started.set()
            stopping.set()
        return {"status": "success", "artifacts": {}}

    worker, outcome = _start(tmp_path, stopping, {TaskType.FETCH: handle})
    try:
        assert first_started.wait(3)
        assert second_started.wait(3)
        assert repo.get("three").status is TaskStatus.PENDING
        release_second.set()
        assert third_started.wait(3)
        assert prepares == ["prepare"]
        assert cleanups == []
        assert worker.is_alive()
    finally:
        release_first.set()
        release_second.set()
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert outcome["processed"] == 3
    assert len({id(state) for state in states}) == 1
    assert cleanups == ["cleanup"]
    assert {task.task_id for task in states[0].tasks} == {"one", "two", "three"}


def test_browser_idle_closes_after_fifteen_seconds_without_sleep(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    clock = [0.0]
    cleanups: list[str] = []
    monkeypatch.setattr(batch_module, "prepare_opencli_browser_session",
                        lambda **_kwargs: {"status": "ready", "browser_headless": True})
    monkeypatch.setattr(batch_module, "cleanup_headless_browser_session",
                        lambda: cleanups.append("cleanup"))
    lifetime = BrowserLifetime(tmp_path, clock=lambda: clock[0])
    task = Task("idle", TaskType.FETCH, "source", "idle")
    assert lifetime.idle_seconds == 15
    assert lifetime.run(task, lambda *_: {"status": "success"})["status"] == "success"
    lifetime.finish_task(task)
    clock[0] = 14.9
    assert not lifetime.close_if_idle()
    assert cleanups == []
    clock[0] = 15.0
    assert lifetime.close_if_idle()
    assert cleanups == ["cleanup"]
    assert not lifetime.close_if_idle(force=True)


def test_browser_lifetime_seconds_allow_environment_overrides(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_BROWSER_BATCH_IDLE_SECONDS", "2.5")
    monkeypatch.setenv("INFOOS_BROWSER_PREPARE_FAILURE_COOLDOWN_SECONDS", "7")
    lifetime = BrowserLifetime(tmp_path)
    assert lifetime.idle_seconds == 2.5
    assert lifetime.failure_cooldown_seconds == 7


def test_browser_prepare_failure_counts_only_fresh_failures_before_blocking(
    tmp_path: Path, monkeypatch,
) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    clock = [0.0]
    prepares: list[str] = []
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: prepares.append("prepare") or {
            "status": "blocked", "browser_headless": False,
        },
    )
    lifetime = BrowserLifetime(tmp_path, clock=lambda: clock[0])
    task = Task("failure", TaskType.FETCH, "source", "failure")

    def forbidden(*_args):
        raise AssertionError("handler must wait for browser readiness")

    first = lifetime.run(task, forbidden, now="2026-10-20T00:00:00Z")
    lifetime.finish_task(task)
    assert first["status"] == "pending"
    assert first["artifacts"]["browser_readiness_deferrals"] == 1
    assert first["artifacts"]["browser_readiness_cached_deferrals"] == 0
    assert first["next_run_at"] == "2026-10-20T00:00:31Z"
    clock[0] = 29.0
    task = replace(task, artifacts_summary=first["artifacts"])
    second = lifetime.run(task, forbidden, now="2026-10-20T00:00:29Z")
    lifetime.finish_task(task)
    assert second["status"] == "pending"
    assert second["artifacts"]["browser_readiness_deferrals"] == 1
    assert second["artifacts"]["browser_readiness_cached_deferrals"] == 1
    assert second["next_run_at"] == "2026-10-20T00:00:31Z"
    assert prepares == ["prepare"]
    clock[0] = 30.0
    task = replace(task, artifacts_summary=second["artifacts"])
    third = lifetime.run(task, forbidden, now="2026-10-20T00:00:30Z")
    lifetime.finish_task(task)
    assert third["status"] == "pending"
    assert third["artifacts"]["browser_readiness_deferrals"] == 2
    assert third["artifacts"]["browser_readiness_cached_deferrals"] == 1
    assert third["next_run_at"] == "2026-10-20T00:01:01Z"
    assert prepares == ["prepare", "prepare"]
    clock[0] = 60.0
    task = replace(task, artifacts_summary=third["artifacts"])
    fourth = lifetime.run(task, forbidden, now="2026-10-20T00:01:00Z")
    lifetime.finish_task(task)
    assert fourth["status"] == "blocked"
    assert fourth["artifacts"]["browser_readiness_deferrals"] == 3
    assert fourth["artifacts"]["browser_readiness_cached_deferrals"] == 1
    assert prepares == ["prepare", "prepare", "prepare"]


def test_slow_startup_prepare_runs_task_on_third_fresh_prepare(tmp_path: Path, monkeypatch) -> None:
    from backend.application.tasks import continuous_browser as browser_module
    from backend.application.workflows import browser_acquisition_batch as batch_module

    clock = [0.0]
    statuses = iter(["blocked", "blocked", "ready"])

    def prepare(**_kwargs) -> dict:
        clock[0] += 2.0
        return {"status": next(statuses), "browser_headless": False}

    monkeypatch.setattr(batch_module, "prepare_opencli_browser_session", prepare)
    start = datetime(2026, 10, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(browser_module, "current_iso", lambda: add_seconds("2026-10-20T00:00:00Z", int(clock[0])))
    lifetime = BrowserLifetime(tmp_path, clock=lambda: clock[0])
    task = Task("startup", TaskType.FETCH, "source", "startup")
    handled: list[str] = []
    results = []
    while not handled:
        result = lifetime.run(task, lambda current, _base: handled.append(current.task_id) or {"status": "success"})
        lifetime.finish_task(task)
        results.append(result)
        if result["status"] != "pending":
            break
        assert result["artifacts"]["browser_readiness_cached_deferrals"] == 0
        clock[0] = (datetime.fromisoformat(result["next_run_at"].replace("Z", "+00:00")) - start).total_seconds()
        task = replace(task, artifacts_summary=result["artifacts"])

    assert handled == ["startup"]
    assert [result["status"] for result in results] == ["pending", "pending", "success"]
    assert task.artifacts_summary["browser_readiness_deferrals"] == 2


def test_cached_prepare_failures_block_at_ten_total_deferrals(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    prepares: list[str] = []
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: prepares.append("prepare") or {"status": "blocked", "browser_headless": False},
    )
    lifetime = BrowserLifetime(tmp_path, clock=lambda: 0.0)
    task = Task("cached", TaskType.FETCH, "source", "cached")

    def forbidden(*_args):
        raise AssertionError("handler must wait for browser readiness")

    statuses = []
    for _ in range(10):
        result = lifetime.run(task, forbidden, now="2026-10-20T00:00:00Z")
        lifetime.finish_task(task)
        statuses.append(result["status"])
        task = replace(task, artifacts_summary=result["artifacts"])

    assert statuses == ["pending"] * 9 + ["blocked"]
    assert task.artifacts_summary["browser_readiness_deferrals"] == 1
    assert task.artifacts_summary["browser_readiness_cached_deferrals"] == 9
    assert prepares == ["prepare"]


def test_complete_checkpoint_replays_without_browser_resource_or_prepare(
    tmp_path: Path, monkeypatch,
) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    repo = _seed_complete_replay(tmp_path)
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not prepare")),
    )
    circuits = RuntimeCircuitRepository(tmp_path)
    circuits.record_failure(
        "platform", "x", occurred_at=current_iso(),
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=60),
    )
    stopping = threading.Event()

    def replay_handler(_task: Task, _base: Path) -> dict:
        assert active_browser_acquisition_batch() is None
        with repo._connect_existing() as conn:
            assert conn.execute(
                "select count(*) from runtime_resource_leases where resource_kind = 'browser_site'"
            ).fetchone()[0] == 0
        stopping.set()
        return {"status": "success", "artifacts": {}}

    assert run_continuous_tasks(
        tmp_path, queue_name="browser_fetch", stop_event=stopping,
        poll_seconds=0.01, handlers={TaskType.FETCH: replay_handler},
    ) == 1
    assert repo.get("replay").status is TaskStatus.SUCCESS


def test_replay_does_not_extend_existing_browser_lifetime(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    repo = _seed_complete_replay(tmp_path)
    clock = [0.0]
    cleaned = threading.Event()
    monkeypatch.setattr(batch_module, "prepare_opencli_browser_session",
                        lambda **_kwargs: {"status": "ready", "browser_headless": True})
    monkeypatch.setattr(batch_module, "cleanup_headless_browser_session", cleaned.set)
    lifetime = BrowserLifetime(tmp_path, clock=lambda: clock[0])
    earlier = Task("earlier", TaskType.FETCH, "source", "earlier")
    lifetime.run(earlier, lambda *_: {"status": "success"})
    lifetime.finish_task(earlier)
    clock[0] = 14.0
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("replay must not prepare")),
    )
    replay_started, release_replay, stopping = (
        threading.Event(), threading.Event(), threading.Event()
    )
    outcome: dict = {}

    def replay_handler(_task: Task, _base: Path) -> dict:
        assert active_browser_acquisition_batch() is None
        replay_started.set()
        assert release_replay.wait(3)
        stopping.set()
        return {"status": "success", "artifacts": {}}

    def run() -> None:
        outcome["processed"] = run_continuous_tasks(
            tmp_path, queue_name="browser_fetch", stop_event=stopping,
            handlers={TaskType.FETCH: replay_handler}, poll_seconds=0.01,
            browser_lifetime=lifetime,
        )

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert replay_started.wait(3)
        clock[0] = 15.0
        assert cleaned.wait(3), "replay must not hold the earlier browser session"
        assert worker.is_alive()
    finally:
        release_replay.set()
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert outcome["processed"] == 1
    assert repo.get("replay").status is TaskStatus.SUCCESS


def test_postclaim_checkpoint_recheck_releases_site_resource(tmp_path: Path, monkeypatch) -> None:
    from backend.application.tasks import continuous_executor, continuous_task_execution
    from backend.application.workflows import browser_acquisition_batch as batch_module

    _source(tmp_path, "x_race")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("race", TaskType.FETCH, "source", "x_race"))
    monkeypatch.setattr(continuous_executor, "complete_checkpoint", lambda *_: False)
    monkeypatch.setattr(continuous_task_execution, "complete_checkpoint", lambda *_: True)
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not prepare")),
    )
    stopping = threading.Event()

    def replay_handler(_task: Task, _base: Path) -> dict:
        assert active_browser_acquisition_batch() is None
        with repo._connect_existing() as conn:
            assert conn.execute(
                "select count(*) from runtime_resource_leases where resource_kind = 'browser_site'"
            ).fetchone()[0] == 0
        stopping.set()
        return {"status": "success", "artifacts": {}}

    assert run_continuous_tasks(
        tmp_path, queue_name="browser_fetch", stop_event=stopping,
        handlers={TaskType.FETCH: replay_handler}, poll_seconds=0.01,
    ) == 1
    assert repo.get("race").status is TaskStatus.SUCCESS


def test_same_site_backlog_does_not_hide_another_site(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    _source(tmp_path, "x_one")
    _source(tmp_path, "youtube_one", platform="youtube")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        *(Task(f"x-{index:03d}", TaskType.FETCH, "source", "x_one")
          for index in range(70)),
        Task("other-site", TaskType.FETCH, "source", "youtube_one"),
    ])
    monkeypatch.setattr(batch_module, "prepare_opencli_browser_session",
                        lambda **_kwargs: {"status": "ready", "browser_headless": False})
    first_started, other_started = threading.Event(), threading.Event()
    release_first, stopping = threading.Event(), threading.Event()

    def handle(task: Task, _base: Path) -> dict:
        if task.task_id == "x-000":
            first_started.set()
            assert release_first.wait(3)
        elif task.task_id == "other-site":
            other_started.set()
            stopping.set()
        else:
            raise AssertionError("same-site waiter ran while its site was leased")
        return {"status": "success", "artifacts": {}}

    worker, outcome = _start(tmp_path, stopping, {TaskType.FETCH: handle})
    try:
        assert first_started.wait(3)
        assert other_started.wait(3)
        assert repo.get("x-001").status is TaskStatus.PENDING
    finally:
        release_first.set()
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert outcome["processed"] == 2


def test_browser_throttle_peek_leaves_task_unclaimed(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from backend.application.tasks import continuous_executor
    from backend.application.workflows import browser_acquisition_batch as batch_module

    _source(tmp_path, "x_throttled")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("throttled", TaskType.FETCH, "source", "x_throttled"))
    stopping = threading.Event()
    peeked: list[str] = []

    def deny(task: Task, _base: Path, *, now):
        peeked.append(task.task_id)
        stopping.set()
        return SimpleNamespace(allowed=False)

    monkeypatch.setattr(continuous_executor, "peek_fetch_throttle_decision", deny)
    monkeypatch.setattr(
        batch_module, "prepare_opencli_browser_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not prepare")),
    )
    assert run_continuous_tasks(
        tmp_path, queue_name="browser_fetch", stop_event=stopping,
        poll_seconds=0.01,
    ) == 0
    assert peeked == ["throttled"]
    persisted = repo.get("throttled")
    assert persisted.status is TaskStatus.PENDING and persisted.claimed_by is None


def test_two_browser_handlers_share_one_repair_leader(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    _source(tmp_path, "x_one")
    _source(tmp_path, "youtube_one", platform="youtube")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        Task("first", TaskType.FETCH, "source", "x_one"),
        Task("second", TaskType.FETCH, "source", "youtube_one"),
    ])
    prepares: list[str] = []
    monkeypatch.setattr(batch_module, "prepare_opencli_browser_session",
                        lambda **_kwargs: prepares.append("prepare") or {
                            "status": "ready", "browser_headless": False,
                        })
    barrier = threading.Barrier(2)
    stopping = threading.Event()
    repairs: list[dict] = []

    def handle(_task: Task, _base: Path) -> dict:
        state = active_browser_acquisition_batch()
        assert state is not None
        barrier.wait(timeout=3)
        repairs.append(repair_browser_acquisition_batch(
            state, original_diagnostic={"category": "browser_session"},
            retried_at="2026-09-23T00:00:00Z",
        ))
        if len(repairs) == 2:
            stopping.set()
        return {"status": "success", "artifacts": {}}

    assert run_continuous_tasks(
        tmp_path, queue_name="browser_fetch", stop_event=stopping,
        poll_seconds=0.01, handlers={TaskType.FETCH: handle},
    ) == 2
    assert len(repairs) == 2
    assert prepares == ["prepare", "prepare"]


def test_browser_prepare_is_heartbeat_protected(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch as batch_module

    _source(tmp_path, "x_slow")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("slow-prepare", TaskType.FETCH, "source", "x_slow"))
    prepare_started, release_prepare = threading.Event(), threading.Event()
    heartbeat_seen, stopping = threading.Event(), threading.Event()
    original_heartbeat = TaskRepository.heartbeat_with_resource

    def heartbeat(self, task_id, **kwargs):
        renewed = original_heartbeat(self, task_id, **kwargs)
        heartbeat_seen.set()
        return renewed

    def prepare(**_kwargs):
        prepare_started.set()
        assert release_prepare.wait(3)
        return {"status": "ready", "browser_headless": False}

    monkeypatch.setattr(TaskRepository, "heartbeat_with_resource", heartbeat)
    monkeypatch.setattr(batch_module, "prepare_opencli_browser_session", prepare)

    def handle(*_args):
        stopping.set()
        return {"status": "success", "artifacts": {}}

    worker, outcome = _start(tmp_path, stopping, {TaskType.FETCH: handle})
    try:
        assert prepare_started.wait(3)
        assert heartbeat_seen.wait(3)
        assert repo.get("slow-prepare").status is TaskStatus.RUNNING
    finally:
        release_prepare.set()
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert outcome["processed"] == 1


def test_browser_fetch_task_worker_runs_continuous_entry(tmp_path: Path, monkeypatch) -> None:
    from backend.application.tasks import continuous_task_execution
    from backend.workers.task_worker import run_task_worker_loop

    _source(tmp_path, "x_worker")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("worker-fetch", TaskType.FETCH, "source", "x_worker"))
    stopping = threading.Event()
    seen: list[str] = []

    def handler(task: Task, _base: Path) -> dict:
        assert active_browser_acquisition_batch() is not None
        seen.append(task.task_id)
        stopping.set()
        return {"status": "success", "artifacts": {}}

    monkeypatch.setattr(continuous_task_execution, "handler_for", lambda _type: handler)
    assert run_task_worker_loop(
        base_dir=tmp_path, queue_name="browser_fetch",
        stop_event=stopping, poll_seconds=0.01,
    ) == 1
    assert seen == ["worker-fetch"]
    assert repo.get("worker-fetch").status is TaskStatus.SUCCESS
