from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.application.tasks.admission import ResourceRequest
from backend.application.tasks.continuous_executor import _execute_claimed, run_continuous_tasks
from backend.application.tasks.execution_clock import add_seconds, current_iso
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.resource_governor import TaskResourcePlan
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


def _source(base_path: Path, source_id: str = "rss_one") -> None:
    SourceRepository(base_path).upsert_source({
        "source_id": source_id, "platform": "rss", "source_type": "feed",
        "identifier": source_id, "display_name": source_id,
        "cadence_hours": 24, "enabled": True,
        "connector_status": "supported",
        "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
    })


def _start_worker(base_path: Path, stopping: threading.Event, *, queue: str,
                  handlers: dict, workers: int = 2) -> tuple[threading.Thread, dict]:
    outcome: dict = {}

    def run() -> None:
        outcome["processed"] = run_continuous_tasks(
            base_path, queue_name=queue, stop_event=stopping,
            max_workers=workers, poll_seconds=0.01, handlers=handlers,
            summary_seconds=0.05,
        )

    worker = threading.Thread(target=run)
    worker.start()
    return worker, outcome


def test_completion_refills_slot_while_slow_peer_runs(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("slow", "fast", "third"):
        repo.enqueue(Task(task_id, TaskType.CARD_BUILD, "card", task_id))
    slow_started = threading.Event()
    fast_started = threading.Event()
    release_slow = threading.Event()
    release_fast = threading.Event()
    third_started = threading.Event()
    stopping = threading.Event()

    def handle(task: Task, _base: Path) -> dict:
        if task.task_id == "slow":
            slow_started.set()
            assert release_slow.wait(3)
        elif task.task_id == "fast":
            fast_started.set()
            assert release_fast.wait(3)
        else:
            third_started.set()
            stopping.set()
        return {"status": "success", "artifacts": {}}

    worker, outcome = _start_worker(
        tmp_path, stopping, queue="card_pipeline",
        handlers={TaskType.CARD_BUILD: handle},
    )
    try:
        assert slow_started.wait(3)
        assert fast_started.wait(3)
        assert repo.get("third").status is TaskStatus.PENDING
        release_fast.set()
        assert third_started.wait(3)
        assert not release_slow.is_set()
        assert worker.is_alive(), "graceful stop waits for the running slow task"
    finally:
        release_fast.set()
        release_slow.set()
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert outcome["processed"] == 3


def test_new_urgent_task_wins_next_free_slot_and_stop_leaves_pending(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("slow", "fast", "old-low"):
        repo.enqueue(Task(task_id, TaskType.VIDEO_EXTRACT_AUDIO, "asset", task_id))
    slow_started = threading.Event()
    fast_started = threading.Event()
    release_slow = threading.Event()
    release_fast = threading.Event()
    urgent_started = threading.Event()
    stopping = threading.Event()
    order: list[str] = []

    def handle(task: Task, _base: Path) -> dict:
        order.append(task.task_id)
        if task.task_id == "slow":
            slow_started.set()
            assert release_slow.wait(3)
        elif task.task_id == "fast":
            fast_started.set()
            assert release_fast.wait(3)
        elif task.task_id == "urgent":
            urgent_started.set()
            stopping.set()
        return {"status": "success", "artifacts": {}}

    worker, outcome = _start_worker(
        tmp_path, stopping, queue="urgent_media", workers=2,
        handlers={TaskType.VIDEO_EXTRACT_AUDIO: handle, TaskType.MEDIA_DOWNLOAD: handle},
    )
    try:
        assert slow_started.wait(3)
        assert fast_started.wait(3)
        repo.enqueue(Task("urgent", TaskType.MEDIA_DOWNLOAD, "asset", "urgent"))
        release_fast.set()
        assert urgent_started.wait(3)
        assert repo.get("old-low").status is TaskStatus.PENDING
    finally:
        release_fast.set()
        release_slow.set()
        stopping.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert set(order[:2]) == {"slow", "fast"}
    assert order[2] == "urgent"
    assert outcome["processed"] == 3


def test_circuit_probe_is_not_acquired_when_claim_fails(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("unclaimed", TaskType.FETCH, "source", "rss_one"))
    stopping = threading.Event()
    admissions: list[str] = []
    original_admit = RuntimeCircuitRepository.admit

    def observe_admit(self, *args, **kwargs):
        admissions.append(kwargs["task_id"])
        return original_admit(self, *args, **kwargs)

    def lose_claim(self, *args, **kwargs):
        stopping.set()
        return []

    monkeypatch.setattr(RuntimeCircuitRepository, "admit", observe_admit)
    monkeypatch.setattr(TaskRepository, "claim_task_ids", lose_claim)
    run_continuous_tasks(tmp_path, queue_name="public_fetch", stop_event=stopping,
                         poll_seconds=0.01)
    assert admissions == []
    assert repo.get("unclaimed").status is TaskStatus.PENDING


def test_circuit_rejection_returns_claim_and_resource_without_attempt(tmp_path: Path) -> None:
    _source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("blocked", TaskType.FETCH, "source", "rss_one"))
    circuits = RuntimeCircuitRepository(tmp_path)
    now = current_iso()
    circuits.record_failure("platform", "rss:rss_one", occurred_at=now,
                            policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=60))
    admission = repo.claim_task_ids_with_resources(
        ["blocked"], worker_id="test-owner", now=now,
        lease_expires_at=add_seconds(now, 300),
        resource_requests={"blocked": ResourceRequest("media_compute", "slot:0")},
        queue_names=["public_fetch"], event_occurred_at=now,
    )
    assert len(admission) == 1
    result, outcome = _execute_claimed(
        admission[0].task, base_path=tmp_path, worker_id="test-owner",
        lease_seconds=300, resource=admission[0].resource,
        handlers={TaskType.FETCH: lambda *_: (_ for _ in ()).throw(AssertionError("must not run"))},
    )
    assert result is None and outcome == "circuit_deferred"
    task = repo.get("blocked")
    assert task.status is TaskStatus.PENDING and task.claimed_by is None
    assert task.attempt == 1 and task.next_run_at == add_seconds(now, 60)
    with repo._connect_existing() as conn:
        assert conn.execute("select count(*) from runtime_resource_leases").fetchone()[0] == 0
    events = TaskEventRepository(tmp_path).list_events(task_id="blocked")
    assert any(event["event_type"] == "recovery_decided" and
               event["payload"]["reason"] == "circuit_open" for event in events)


def test_pending_result_releases_owned_half_open_probe(tmp_path: Path) -> None:
    _source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("probe", TaskType.FETCH, "source", "rss_one"))
    circuits = RuntimeCircuitRepository(tmp_path)
    now = current_iso()
    circuits.record_failure("platform", "rss:rss_one", occurred_at=add_seconds(now, -2),
                            policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=1))
    claimed = repo.claim_task_ids(
        ["probe"], worker_id="test-owner", now=now,
        lease_expires_at=add_seconds(now, 300), queue_names=["public_fetch"],
    )[0]
    result, outcome = _execute_claimed(
        claimed, base_path=tmp_path, worker_id="test-owner", lease_seconds=300,
        resource=None, handlers={TaskType.FETCH: lambda *_: {
            "status": "pending", "next_run_at": add_seconds(now, 30), "artifacts": {},
        }},
    )
    assert outcome is None and result["status"] == "pending"
    state = circuits.get("platform", "rss:rss_one")
    assert state["state"] == "half_open"
    assert state["probe_owner_token"] is None


def test_probe_renewal_writes_only_below_half_lease(tmp_path: Path, monkeypatch) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=1, probe_lease_seconds=10)
    start = "2026-09-23T00:00:00Z"
    circuits.record_failure("platform", "rss:rss_one", occurred_at=start, policy=policy)
    admitted = circuits.admit("platform", "rss:rss_one", owner_token="owner", now=add_seconds(start, 1), policy=policy)
    assert admitted.probe
    updates: list[str] = []
    original_connection = circuits._connection

    def traced_connection():
        conn = original_connection()
        conn.set_trace_callback(lambda sql: updates.append(sql) if "update runtime_circuits" in sql.lower() else None)
        return conn

    monkeypatch.setattr(circuits, "_connection", traced_connection)
    assert circuits.renew_probe("platform", "rss:rss_one", owner_token="owner", now=add_seconds(start, 2), lease_seconds=10)
    assert circuits.renew_probe("platform", "rss:rss_one", owner_token="owner", now=add_seconds(start, 6), lease_seconds=10)
    assert updates == []
    assert circuits.renew_probe("platform", "rss:rss_one", owner_token="owner", now=add_seconds(start, 7), lease_seconds=10)
    assert len(updates) == 1
    assert circuits.renew_probe("platform", "rss:rss_one", owner_token="owner", now=add_seconds(start, 8), lease_seconds=10)
    assert len(updates) == 1
    assert not circuits.renew_probe("platform", "rss:rss_one", owner_token="other", now=add_seconds(start, 8), lease_seconds=10)


def test_running_task_heartbeat_renews_its_probe(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("long-probe", TaskType.FETCH, "source", "rss_one"))
    circuits = RuntimeCircuitRepository(tmp_path)
    start = current_iso()
    circuits.record_failure(
        "platform", "rss:rss_one", occurred_at=add_seconds(start, -2),
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=1),
    )
    claimed = repo.claim_task_ids(
        ["long-probe"], worker_id="probe-owner", now=start,
        lease_expires_at=add_seconds(start, 300), queue_names=["public_fetch"],
    )[0]
    monkeypatch.setenv("INFOOS_PLATFORM_CIRCUIT_PROBE_LEASE_SECONDS", "10")
    monkeypatch.setattr(
        "backend.application.tasks.processor_execution.heartbeat_interval_seconds",
        lambda _lease: 0.01,
    )
    clock = [start]
    monkeypatch.setattr("backend.application.tasks.processor_execution.current_iso", lambda: clock[0])
    entered = threading.Event()
    renewed = threading.Event()
    release = threading.Event()
    observed: dict = {}
    original_renew = RuntimeCircuitRepository.renew_probe

    def observe_renew(self, *args, **kwargs):
        result = original_renew(self, *args, **kwargs)
        if result and self.get("platform", "rss:rss_one")["probe_lease_expires_at"] > add_seconds(start, 10):
            renewed.set()
        return result

    monkeypatch.setattr(RuntimeCircuitRepository, "renew_probe", observe_renew)

    def handle(*_args):
        entered.set()
        assert release.wait(3)
        return {"status": "success", "artifacts": {}}

    def run():
        observed["result"] = _execute_claimed(
            claimed, base_path=tmp_path, worker_id="probe-owner",
            lease_seconds=300, resource=None,
            handlers={TaskType.FETCH: handle},
        )

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        clock[0] = add_seconds(start, 6)
        assert renewed.wait(3)
        assert thread.is_alive()
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert observed["result"][0]["status"] == "success"


def test_lost_probe_prevents_finalization_and_a1_recovers_lease(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("lost-probe", TaskType.FETCH, "source", "rss_one"))
    circuits = RuntimeCircuitRepository(tmp_path)
    start = current_iso()
    circuits.record_failure(
        "platform", "rss:rss_one", occurred_at=add_seconds(start, -2),
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=1),
    )
    claimed = repo.claim_task_ids(
        ["lost-probe"], worker_id="probe-owner", now=start,
        lease_expires_at=add_seconds(start, 2), queue_names=["public_fetch"],
    )[0]
    monkeypatch.setattr(
        "backend.application.tasks.processor_execution.heartbeat_interval_seconds",
        lambda _lease: 0.01,
    )
    entered, probe_lost, release = threading.Event(), threading.Event(), threading.Event()
    observed: dict = {}
    original_renew = RuntimeCircuitRepository.renew_probe

    def observe_renew(self, *args, **kwargs):
        owned = original_renew(self, *args, **kwargs)
        if not owned:
            probe_lost.set()
        return owned

    monkeypatch.setattr(RuntimeCircuitRepository, "renew_probe", observe_renew)

    def handle(*_args):
        entered.set()
        assert release.wait(3)
        return {"status": "success", "artifacts": {}}

    def run():
        observed["result"] = _execute_claimed(
            claimed, base_path=tmp_path, worker_id="probe-owner",
            lease_seconds=2, resource=None,
            handlers={TaskType.FETCH: handle},
        )

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(3)
        owner = circuits.get("platform", "rss:rss_one")["probe_owner_token"]
        assert owner
        assert circuits.release_probe("platform", "rss:rss_one", owner_token=owner, now=current_iso())
        assert probe_lost.wait(3)
    finally:
        release.set()
        thread.join(timeout=3)
    assert not thread.is_alive()
    assert observed["result"][0]["status"] == "running"
    assert repo.get("lost-probe").status is TaskStatus.RUNNING
    assert repo.recover_expired_leases(
        add_seconds(current_iso(), 3), base_dir=tmp_path,
    ) == 1
    recovered = repo.get("lost-probe")
    assert recovered.status is TaskStatus.PENDING and recovered.attempt == 2


def test_due_page_keyset_reaches_items_after_first_page(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id in ("first", "second", "third"):
        repo.enqueue(Task(task_id, TaskType.CARD_BUILD, "card", task_id))
    seen: list[str] = []
    cursor = None
    for _ in range(3):
        page, cursor = repo.list_due_page(
            now=current_iso(), queue_names=["card_pipeline"], limit=1,
            after=cursor,
        )
        assert len(page) == 1 and cursor is not None
        seen.append(page[0].task_id)
    assert seen == ["first", "second", "third"]


def test_refill_carries_cursor_past_blocked_head_pages(tmp_path: Path, monkeypatch) -> None:
    from backend.application.tasks import continuous_executor

    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        *(Task(f"blocked-{index:03d}", TaskType.CARD_BUILD, "card", str(index))
          for index in range(200)),
        Task("tail", TaskType.CARD_BUILD, "card", "tail"),
    ])
    stopping = threading.Event()
    monkeypatch.setattr(
        continuous_executor, "resource_plan_for",
        lambda task: TaskResourcePlan(
            allowed=task.task_id == "tail",
            defer_reason=None if task.task_id == "tail" else "test_pressure",
        ),
    )

    def handle(*_args):
        stopping.set()
        return {"status": "success", "artifacts": {}}

    assert run_continuous_tasks(
        tmp_path, queue_name="card_pipeline", stop_event=stopping,
        poll_seconds=0.01, handlers={TaskType.CARD_BUILD: handle},
    ) == 1
    assert repo.get("tail").status is TaskStatus.SUCCESS


def test_task_worker_routes_non_browser_queue_to_continuous_entry(tmp_path: Path, monkeypatch) -> None:
    from backend.workers import task_worker

    calls: list[str] = []
    stopping = threading.Event()

    def continuous(base_path, *, queue_name, stop_event, **_kwargs):
        calls.append(queue_name)
        assert stop_event is stopping
        stop_event.set()
        return 4

    monkeypatch.setattr(task_worker, "run_continuous_tasks", continuous)
    result = task_worker.run_task_worker_loop(
        base_dir=tmp_path, queue_name="card_pipeline", stop_event=stopping,
    )
    assert result == 4
    assert calls == ["card_pipeline"]


def test_task_worker_browser_queue_uses_continuous_entry(tmp_path: Path, monkeypatch) -> None:
    from backend.workers import task_worker

    stopping = threading.Event()
    calls: list[str] = []

    def continuous(_base_path, *, queue_name, stop_event, **_kwargs):
        calls.append(queue_name)
        stop_event.set()
        return 1

    monkeypatch.setattr(task_worker, "run_continuous_tasks", continuous)
    assert task_worker.run_task_worker_loop(
        base_dir=tmp_path, queue_name="browser_fetch", stop_event=stopping,
    ) == 1
    assert calls == ["browser_fetch"]


def test_browser_lane_in_other_queue_uses_continuous_entry(tmp_path: Path) -> None:
    SourceRepository(tmp_path).upsert_source({
        "source_id": "x_one", "platform": "x", "source_type": "creator",
        "identifier": "x_one", "display_name": "x_one",
        "cadence_hours": 24, "enabled": True,
        "connector_status": "supported",
        "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
    })
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("browser-in-public", TaskType.FETCH, "source", "x_one"))
    with repo._connect() as conn:
        conn.execute(
            "update tasks set queue_name = 'public_fetch' where task_id = ?",
            ("browser-in-public",),
        )
    stopping = threading.Event()
    calls: list[str] = []

    def handle(task, _base_path):
        calls.append(task.task_id)
        stopping.set()
        return {"status": "success", "artifacts": {}}

    assert run_continuous_tasks(
        tmp_path, queue_name="public_fetch", stop_event=stopping,
        poll_seconds=0.01, handlers={TaskType.FETCH: handle},
    ) == 1
    assert calls == ["browser-in-public"]
    assert repo.get("browser-in-public").status is TaskStatus.SUCCESS


def test_period_summary_has_bounded_counts_and_stop_flag(tmp_path: Path, caplog) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("one", TaskType.CARD_BUILD, "card", "one"))
    stopping = threading.Event()

    def handle(*_args):
        stopping.set()
        return {"status": "success", "artifacts": {}}

    with caplog.at_level("INFO", logger="infoos"):
        processed = run_continuous_tasks(
            tmp_path, queue_name="card_pipeline", stop_event=stopping,
            handlers={TaskType.CARD_BUILD: handle}, poll_seconds=0.01,
        )
    assert processed == 1
    summaries = [json.loads(record.message.split("task_worker_period_summary ", 1)[1])
                 for record in caplog.records if "task_worker_period_summary " in record.message]
    assert summaries
    final = summaries[-1]
    for field in ("processed", "succeeded", "failed", "requeued", "recovered",
                  "claims_failed", "circuit_deferred", "due_query_count",
                  "resource_pressure_reasons", "slots_busy", "slots_capacity",
                  "enqueue_to_claim_p50", "enqueue_to_claim_p95"):
        assert field in final
    assert final["processed"] == final["succeeded"] == 1
    assert final["stop_requested"] is True
