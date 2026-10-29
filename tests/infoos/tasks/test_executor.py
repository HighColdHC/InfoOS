from __future__ import annotations

from pathlib import Path
import subprocess
import threading
import time

from backend.infrastructure.storage.repositories import SourceRepository, TaskEventRepository
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks, run_tasks_by_ids


def _upsert_executor_source(
    base_dir: Path,
    *,
    source_id: str,
    platform: str,
    source_type: str,
    identifier: str,
    raw_metadata: dict | None = None,
) -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": platform,
            "source_type": source_type,
            "identifier": identifier,
            "display_name": source_id,
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": source_id,
            "raw_metadata": raw_metadata or {},
        }
    )


def _isolate_browser_acquisition_batch(monkeypatch) -> None:
    """Keep injected task-handler tests off the machine browser lifecycle."""
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": False},
    )


def test_run_due_tasks_consumes_only_requested_task_types(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_rss_1",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss_1",
            next_run_at="2026-06-23T00:00:00Z",
        )
    )
    repo.enqueue(
        Task(
            task_id="content_enrich_card_1",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card_1",
            next_run_at="2026-06-23T00:00:00Z",
        )
    )

    result = run_due_tasks(
        tmp_path,
        now="2026-06-23T00:01:00Z",
        task_types=(TaskType.FETCH,),
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"created_or_updated": "updated"},
            },
            TaskType.CONTENT_ENRICH: lambda task, base_dir: (_ for _ in ()).throw(
                AssertionError("enrichment task should not run in fetch queue")
            ),
        },
    )

    assert result["processed"] == 1
    assert repo.get("fetch_rss_1").status == TaskStatus.SUCCESS
    assert repo.get("content_enrich_card_1").status == TaskStatus.PENDING


def test_worker_can_process_independent_tasks_concurrently(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    for idx in range(3):
        repo.enqueue(
            Task(
                task_id=f"fetch_rss_{idx}",
                task_type=TaskType.FETCH,
                target_kind="source",
                target_id=f"rss_{idx}",
            )
        )

    started: list[float] = []

    def slow_fetch(task, base_dir):
        started.append(time.perf_counter())
        time.sleep(0.2)
        return {"status": "success", "artifacts": {"created_or_updated": "updated"}}

    t0 = time.perf_counter()
    result = run_due_tasks(
        tmp_path,
        limit=3,
        max_workers=3,
        handlers={TaskType.FETCH: slow_fetch},
    )
    elapsed = time.perf_counter() - t0

    assert result["succeeded"] == 3
    assert result["worker_count"] == 3
    assert elapsed < 0.45
    assert max(started) - min(started) < 0.15


def test_browser_lane_allows_two_platforms_by_default(tmp_path: Path, monkeypatch):
    rate_limit_disabled = {"fetch_rate_policy": {"enabled": False}}
    _upsert_executor_source(
        tmp_path, source_id="x_a", platform="x", source_type="creator", identifier="a", raw_metadata=rate_limit_disabled,
    )
    _upsert_executor_source(
        tmp_path, source_id="youtube_b", platform="youtube", source_type="keyword", identifier="b", raw_metadata=rate_limit_disabled,
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    for source_id in ("x_a", "youtube_b"):
        repo.enqueue(Task(
            task_id=f"fetch_{source_id}",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id=source_id,
        ))
    monkeypatch.delenv("INFOOS_BROWSER_FETCH_WORKERS", raising=False)
    prepares: list[str] = []
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: prepares.append("prepare") or {"status": "ready", "browser_headless": False},
    )
    active = 0
    peak_active = 0
    guard = threading.Lock()

    def browser_fetch(task, base_dir):
        nonlocal active, peak_active
        from backend.application.workflows.browser_acquisition_batch import active_browser_acquisition_batch

        assert active_browser_acquisition_batch() is not None
        with guard:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.1)
        with guard:
            active -= 1
        return {"status": "success", "artifacts": {}}

    result = run_due_tasks(
        tmp_path,
        max_workers=8,
        handlers={TaskType.FETCH: browser_fetch},
    )

    assert result["succeeded"] == 2
    assert peak_active == 2
    assert prepares == ["prepare"]
    assert result["lane_worker_counts"] == {"browser_acquisition": 2}


def test_browser_lane_same_site_tasks_remain_serial(tmp_path: Path, monkeypatch):
    rate_limit_disabled = {"fetch_rate_policy": {"enabled": False}}
    for source_id in ("x_a", "x_b"):
        _upsert_executor_source(
            tmp_path, source_id=source_id, platform="x", source_type="creator",
            identifier=source_id, raw_metadata=rate_limit_disabled,
        )
    repo = TaskRepository.for_base_dir(tmp_path)
    for source_id in ("x_a", "x_b"):
        repo.enqueue(Task(f"fetch_{source_id}", TaskType.FETCH, "source", source_id))
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": False},
    )
    active = 0
    peak_active = 0
    guard = threading.Lock()

    def slow_fetch(_task, _base_dir):
        nonlocal active, peak_active
        from backend.infrastructure.opencli.tab_lease import opencli_tab_lease

        with opencli_tab_lease("twitter"):
            with guard:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.08)
            with guard:
                active -= 1
        return {"status": "success", "artifacts": {}}

    result = run_due_tasks(tmp_path, handlers={TaskType.FETCH: slow_fetch})

    assert result["succeeded"] == 2
    assert peak_active == 1


def test_browser_lane_can_fall_back_to_one_worker(monkeypatch):
    from backend.application.tasks.executor import browser_fetch_worker_count

    monkeypatch.setenv("INFOOS_BROWSER_FETCH_WORKERS", "1")
    assert browser_fetch_worker_count() == 1


def test_dedicated_browser_queue_selects_x_and_later_zsxq(tmp_path: Path, monkeypatch):
    rate_limit_disabled = {"fetch_rate_policy": {"enabled": False}}
    for index in range(5):
        _upsert_executor_source(
            tmp_path,
            source_id=f"x_{index}",
            platform="x",
            source_type="creator",
            identifier=f"x_{index}",
            raw_metadata=rate_limit_disabled,
        )
    _upsert_executor_source(
        tmp_path,
        source_id="zsxq_later",
        platform="zsxq",
        source_type="keyword",
        identifier="AI",
        raw_metadata={
            "group_id": "1824528822",
            "fetch_rate_policy": {"enabled": False},
        },
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    for source_id in [*(f"x_{index}" for index in range(5)), "zsxq_later"]:
        repo.enqueue(Task(
            task_id=f"fetch_{source_id}",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id=source_id,
            next_run_at="2026-08-12T00:00:00Z",
        ))
    _isolate_browser_acquisition_batch(monkeypatch)
    called: list[str] = []

    result = run_due_tasks(
        tmp_path,
        now="2026-08-12T00:00:00Z",
        limit=2,
        queue_names=["browser_fetch"],
        handlers={
            TaskType.FETCH: lambda task, _base_dir: called.append(task.target_id)
            or {"status": "success", "artifacts": {}}
        },
    )

    assert result["succeeded"] == 2
    assert set(called) == {"x_0", "zsxq_later"}


def test_dedicated_browser_queue_attempts_site_once_per_refill(tmp_path: Path, monkeypatch):
    rate_limit_disabled = {"fetch_rate_policy": {"enabled": False}}
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(4):
        source_id = f"x_only_{index}"
        _upsert_executor_source(
            tmp_path,
            source_id=source_id,
            platform="x",
            source_type="creator",
            identifier=source_id,
            raw_metadata=rate_limit_disabled,
        )
        repo.enqueue(Task(f"fetch_{source_id}", TaskType.FETCH, "source", source_id))
    _isolate_browser_acquisition_batch(monkeypatch)

    from backend.application.tasks.synchronous_batch import SynchronousBatch

    rounds = [0]
    claims = []
    original_page = SynchronousBatch.page
    original_claim = TaskRepository.claim_task_ids_with_resources

    def page(self, **kwargs):
        if kwargs["after"] is None:
            rounds[0] += 1
        return original_page(self, **kwargs)

    def claim(self, task_ids, **kwargs):
        claims.append(rounds[0])
        return original_claim(self, task_ids, **kwargs)

    monkeypatch.setattr(SynchronousBatch, "page", page)
    monkeypatch.setattr(TaskRepository, "claim_task_ids_with_resources", claim)
    result = run_due_tasks(
        tmp_path,
        limit=2,
        queue_names=["browser_fetch"],
        handlers={TaskType.FETCH: lambda _task, _base_dir: {"status": "success", "artifacts": {}}},
    )

    events = TaskEventRepository(tmp_path).list_events(limit=100)
    assert result["processed"] == 2
    assert sum(event["event_type"] == "claimed" for event in events) == 2
    assert len(claims) == len(set(claims)) == 2
    assert not any(event["event_type"] == "resource_deferred" for event in events)


def test_browser_refill_caches_lane_by_task(tmp_path: Path, monkeypatch):
    from backend.application.tasks import candidate_lane_cache
    from backend.application.tasks.lanes import ExecutionLane

    _upsert_executor_source(
        tmp_path, source_id="x_same", platform="x", source_type="creator", identifier="same",
        raw_metadata={"fetch_rate_policy": {"enabled": False}},
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = [Task(f"same_source_{index}", TaskType.FETCH, "source", "x_same") for index in range(3)]
    repo.enqueue_many(tasks)
    site_calls = []

    def lane(task, _base):
        site_calls.append(task.task_id)
        return ExecutionLane.BROWSER_ACQUISITION, "twitter"

    monkeypatch.setattr(candidate_lane_cache, "execution_lane_and_site_for", lane)
    result = run_due_tasks(
        tmp_path, limit=2, queue_names=["browser_fetch"],
        handlers={TaskType.FETCH: lambda *_: {"status": "success", "artifacts": {}}},
    )
    assert result["succeeded"] == 2
    assert site_calls == [task.task_id for task in tasks]


def test_browser_refill_scans_same_site_after_throttled_identity(tmp_path: Path, monkeypatch):
    from backend.application.tasks.fetch_rate_limit import FetchThrottleDecision

    for name in ("x_identity_a", "x_identity_b"):
        _upsert_executor_source(
            tmp_path, source_id=name, platform="x", source_type="creator", identifier=name,
            raw_metadata={"fetch_rate_policy": {"enabled": False}},
        )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([
        Task("twitter_identity_a", TaskType.FETCH, "source", "x_identity_a"),
        Task("twitter_identity_b", TaskType.FETCH, "source", "x_identity_b"),
    ])
    monkeypatch.setattr(
        "backend.application.tasks.continuous_executor.peek_fetch_throttle_decision",
        lambda task, *_args, **_kwargs: FetchThrottleDecision(allowed=task.target_id == "x_identity_b"),
    )
    result = run_due_tasks(
        tmp_path, limit=1, queue_names=["browser_fetch"],
        handlers={TaskType.FETCH: lambda *_: {"status": "success", "artifacts": {}}},
    )
    assert [item["task_id"] for item in result["results"]] == ["twitter_identity_b"]
    assert repo.get("twitter_identity_a").claimed_by is None


def test_dedicated_browser_queue_rate_peek_skips_claim_until_window(tmp_path: Path, monkeypatch):
    from backend.application.tasks.fetch_rate_limit import reserve_fetch_slot

    _upsert_executor_source(
        tmp_path,
        source_id="x_throttled",
        platform="x",
        source_type="creator",
        identifier="throttled",
        raw_metadata={
            "fetch_rate_policy": {
                "min_gap_seconds": 60,
                "jitter_seconds": 0,
                "hourly_budget": 10,
                "daily_budget": 100,
            }
        },
    )
    reserve_fetch_slot(tmp_path, source_id="x_throttled", now="2026-08-12T00:00:00Z")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "fetch_x_throttled",
        TaskType.FETCH,
        "source",
        "x_throttled",
        next_run_at="2026-08-12T00:00:00Z",
    ))
    _isolate_browser_acquisition_batch(monkeypatch)
    handler = lambda _task, _base_dir: {"status": "success", "artifacts": {}}
    with repo._connect_existing() as conn:
        throttle_before = dict(conn.execute(
            "select * from fetch_rate_limit_state where throttle_key = 'x:x_default'"
        ).fetchone())

    blocked = run_due_tasks(
        tmp_path,
        now="2026-08-12T00:00:30Z",
        limit=2,
        queue_names=["browser_fetch"],
        handlers={TaskType.FETCH: handler},
    )

    assert blocked["processed"] == 0
    assert repo.get("fetch_x_throttled").status == TaskStatus.PENDING
    assert TaskEventRepository(tmp_path).list_events(task_id="fetch_x_throttled") == []
    with repo._connect_existing() as conn:
        throttle_after = dict(conn.execute(
            "select * from fetch_rate_limit_state where throttle_key = 'x:x_default'"
        ).fetchone())
    assert throttle_after == throttle_before

    allowed = run_due_tasks(
        tmp_path,
        now="2026-08-12T00:01:00Z",
        limit=2,
        queue_names=["browser_fetch"],
        handlers={TaskType.FETCH: handler},
    )

    assert allowed["succeeded"] == 1
    assert repo.get("fetch_x_throttled").status == TaskStatus.SUCCESS


def test_browser_lane_custom_fetch_handler_keeps_browser_batch_lifecycle(tmp_path: Path, monkeypatch):
    _upsert_executor_source(
        tmp_path, source_id="x_a", platform="x", source_type="creator", identifier="a",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch_x_a", TaskType.FETCH, "source", "x_a"))
    lifecycle_called = []

    class BrowserBatch:
        def __enter__(self):
            from types import SimpleNamespace

            lifecycle_called.append("enter")
            return SimpleNamespace(prepare_result={"status": "ready"}, add_task=lambda _task: None)

        def __exit__(self, *_args):
            lifecycle_called.append("exit")

    monkeypatch.setattr(
        "backend.application.tasks.continuous_browser.browser_acquisition_batch",
        lambda *args, **kwargs: BrowserBatch(),
    )

    result = run_due_tasks(
        tmp_path,
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"created_or_updated": "updated"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert result["lane_worker_counts"] == {"browser_acquisition": 2}
    assert lifecycle_called == ["enter", "exit"]


def test_public_tasks_continue_while_browser_lane_is_blocked(tmp_path: Path, monkeypatch):
    _upsert_executor_source(
        tmp_path,
        source_id="rss_a",
        platform="rss",
        source_type="feed",
        identifier="https://example.com/feed.xml",
    )
    _upsert_executor_source(tmp_path, source_id="x_a", platform="x", source_type="creator", identifier="a")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch_rss", TaskType.FETCH, "source", "rss_a"))
    repo.enqueue(Task("fetch_x", TaskType.FETCH, "source", "x_a"))
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **kwargs: {"status": "ready", "browser_headless": False},
    )
    browser_started = threading.Event()
    public_finished = threading.Event()

    def fetch(task, base_dir):
        if task.target_id == "x_a":
            browser_started.set()
            assert public_finished.wait(timeout=1), "public lane did not continue while browser lane was blocked"
        else:
            assert browser_started.wait(timeout=1), "browser lane did not start"
            public_finished.set()
        return {"status": "success", "artifacts": {}}

    result = run_due_tasks(tmp_path, max_workers=1, handlers={TaskType.FETCH: fetch})

    assert result["succeeded"] == 2
    assert public_finished.is_set()


def test_worker_recovers_expired_semantic_batch_lease(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="semantic_job_stale",
            task_type=TaskType.SEMANTIC_EXTRACT,
            target_kind="semantic_batch",
            target_id="semantic_job_stale",
            status=TaskStatus.RUNNING,
            claimed_by="dead-worker",
            lease_expires_at="2026-06-23T02:00:00Z",
            next_run_at="2026-06-23T00:00:00Z",
            artifacts_summary={"status": "running", "processed": 1, "total": 4},
        )
    )

    result = run_due_tasks(
        tmp_path,
        now="2099-06-23T02:10:00Z",
        handlers={
            TaskType.SEMANTIC_EXTRACT: lambda task, base_dir: {
                "status": "success",
                "artifacts": {"recovered": True},
            }
        },
    )

    assert result["succeeded"] == 0
    assert result["recovered"] == 1
    assert repo.get("semantic_job_stale").next_run_at == "2099-06-23T02:12:00Z"
    result = run_due_tasks(
        tmp_path,
        now="2099-06-23T02:12:00Z",
        handlers={TaskType.SEMANTIC_EXTRACT: lambda task, base_dir: {
            "status": "success", "artifacts": {"recovered": True},
        }},
    )
    assert result["succeeded"] == 1
    assert repo.get("semantic_job_stale").status == TaskStatus.SUCCESS


def test_run_due_tasks_claims_before_execution(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="claimed-before-handler",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))

    def handler(task, base_dir):
        persisted = repo.get(task.task_id)
        assert persisted is not None
        assert persisted.status == TaskStatus.RUNNING
        assert persisted.claimed_by is not None
        assert persisted.claimed_by.startswith("task-worker-")
        return {"status": "success", "artifacts": {"created_or_updated": "updated"}}

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={TaskType.FETCH: handler},
    )

    assert result["succeeded"] == 1
    persisted = repo.get("claimed-before-handler")
    assert persisted is not None
    assert persisted.status == TaskStatus.SUCCESS
    assert persisted.artifacts_summary["downstream_task_ids"] == result["results"][0]["downstream_task_ids"]


def test_executor_records_claim_complete_retry_and_recovery(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="event-success",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-success",
        artifacts_summary={"chain_id": "chain-events"},
    ))
    repo.enqueue(Task(
        task_id="event-retry",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-retry",
        artifacts_summary={"chain_id": "chain-events"},
    ))
    repo.enqueue(Task(
        task_id="event-recovered",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-recovered",
        status=TaskStatus.RUNNING,
        claimed_by="stale-worker",
        lease_expires_at="2026-07-10T07:00:00Z",
        artifacts_summary={"chain_id": "chain-events"},
    ))

    run_due_tasks(
        tmp_path,
        now="2026-07-10T08:00:00Z",
        handlers={
            TaskType.CONTENT_ENRICH: lambda task, _base_dir: (
                {"status": "failed", "error": "connection timed out"}
                if task.task_id == "event-retry"
                else {"status": "success", "artifacts": {}}
            )
        },
    )

    run_due_tasks(
        tmp_path,
        now="2026-07-10T08:02:00Z",
        task_types=(TaskType.CONTENT_ENRICH,),
        handlers={TaskType.CONTENT_ENRICH: lambda task, _base_dir: {
            "status": "success", "artifacts": {},
        }},
    )

    events = TaskEventRepository(tmp_path).list_events(chain_id="chain-events")
    by_task = {}
    for event in events:
        by_task.setdefault(event["task_id"], []).append(event["event_type"])
    assert by_task["event-success"] == ["claimed", "completed"]
    assert by_task["event-retry"][:2] == ["claimed", "retry_scheduled"]
    assert by_task["event-recovered"] == ["lease_recovered", "claimed", "completed"]


def test_automatic_retry_event_keeps_bounded_subprocess_diagnostic(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="event-sanitized-retry",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-sanitized",
    ))
    secret = "super-secret-token"
    url = f"https://private.example/items?token={secret}"

    def handler(_task, _base_dir):
        raise subprocess.CalledProcessError(
            17,
            ["opencli", "--token", secret, url],
            output=f"raw stdout {secret}",
            stderr=f"raw stderr {secret}",
        )

    result = run_tasks_by_ids(
        tmp_path,
        ["event-sanitized-retry"],
        now="2026-07-10T08:00:00Z",
        handlers={TaskType.CONTENT_ENRICH: handler},
    )

    assert result["requeued"] == 1
    event = next(
        event
        for event in TaskEventRepository(tmp_path).list_events(
            task_id="event-sanitized-retry"
        )
        if event["event_type"] == "retry_scheduled"
    )
    diagnostic = event["payload"]["diagnostic"]
    assert diagnostic["type"] == "CalledProcessError"
    assert diagnostic["code"] == "opencli_command_failed"
    assert diagnostic["exit_code"] == 17
    encoded = str(event["payload"])
    assert secret not in encoded
    assert url not in encoded
    assert "--token" not in encoded
    assert "raw stdout" not in encoded
    assert "raw stderr" not in encoded


def test_structured_fetch_failure_event_includes_redacted_stderr_summary(tmp_path: Path):
    from backend.application.workflows.acquisition_failures import (
        AcquisitionFailureError,
        classify_acquisition_failure,
    )
    from backend.infrastructure.connectors.opencli import OpenCLICommandError

    repo = TaskRepository.for_base_dir(tmp_path)
    cases = (
        (1, "adapter failed", "failed"),
        (3, "adapter failed", "retry_scheduled"),
        (3, "login required", "blocked"),
    )
    for index, (max_attempts, message, event_type) in enumerate(cases):
        task_id = f"stderr-event-{index}"
        repo.enqueue(Task(
            task_id=task_id, task_type=TaskType.CONTENT_ENRICH,
            target_kind="card", target_id=task_id, max_attempts=max_attempts,
        ))
        error = OpenCLICommandError(1, stderr_summary=f"{message} token=private-secret")
        failure = classify_acquisition_failure(
            source={"platform": "douyin", "source_type": "creator"},
            result=None, error=error,
        )
        assert failure is not None

        def handler(_task, _base_dir):
            raise AcquisitionFailureError(failure)

        run_tasks_by_ids(
            tmp_path, [task_id], now="2026-09-27T08:00:00Z",
            handlers={TaskType.CONTENT_ENRICH: handler},
        )
        event = next(
            event for event in TaskEventRepository(tmp_path).list_events(task_id=task_id)
            if event["event_type"] == event_type
        )
        summary = event["payload"]["diagnostic"]["stderr_summary"]
        assert summary == f"{message} token=[REDACTED]"
        assert "private-secret" not in str(event["payload"])


def test_task_event_inherits_chain_id(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="event-chain",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-chain",
        artifacts_summary={"chain_id": "source_run_chain"},
    ))

    run_due_tasks(
        tmp_path,
        handlers={TaskType.CONTENT_ENRICH: lambda _task, _base_dir: {"status": "success", "artifacts": {}}},
    )

    assert {event["chain_id"] for event in TaskEventRepository(tmp_path).list_events(task_id="event-chain")} == {"source_run_chain"}


def test_heartbeat_does_not_flood_event_table(tmp_path: Path, monkeypatch):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="heartbeat-events",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-heartbeat",
    ))
    monkeypatch.setenv("INFOOS_TASK_LEASE_SECONDS", "2")
    monkeypatch.setenv("INFOOS_TASK_HEARTBEAT_SECONDS", "1")

    def handler(task, _base_dir):
        time.sleep(1.2)
        with repo._connect() as conn:
            conn.execute("update tasks set claimed_by = ? where task_id = ?", ("other-worker", task.task_id))
        return {"status": "success", "artifacts": {}}

    run_due_tasks(tmp_path, handlers={TaskType.CONTENT_ENRICH: handler})

    events = TaskEventRepository(tmp_path).list_events(task_id="heartbeat-events")
    assert [event["event_type"] for event in events] == ["claimed", "lease_lost"]


def test_executor_heartbeats_long_running_task(tmp_path: Path, monkeypatch):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="heartbeat-task",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))
    heartbeat_seen = threading.Event()
    original_heartbeat = TaskRepository.heartbeat

    def observe_heartbeat(self, task_id, **kwargs):
        result = original_heartbeat(self, task_id, **kwargs)
        if task_id == "heartbeat-task":
            heartbeat_seen.set()
        return result

    monkeypatch.setattr(TaskRepository, "heartbeat", observe_heartbeat)

    def handler(task, base_dir):
        assert heartbeat_seen.wait(timeout=1)
        return {"status": "success", "artifacts": {"created_or_updated": "updated"}}

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={TaskType.FETCH: handler},
    )

    assert result["succeeded"] == 1
    assert heartbeat_seen.is_set()


def test_executor_does_not_complete_after_losing_lease(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="lost-lease",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))

    def handler(task, base_dir):
        with repo._connect() as conn:
            conn.execute(
                "update tasks set claimed_by = ? where task_id = ?",
                ("replacement-worker", task.task_id),
            )
        return {"status": "success", "artifacts": {"enriched": True}}

    run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={TaskType.CONTENT_ENRICH: handler},
    )

    persisted = repo.get("lost-lease")
    assert persisted is not None
    assert persisted.status == TaskStatus.RUNNING
    assert persisted.claimed_by == "replacement-worker"
    assert repo.get("media_download:card-1") is None


def test_executor_does_not_overwrite_reclaimed_retry_artifacts(tmp_path: Path, monkeypatch):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="retry-reclaimed",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))
    original = TaskRepository.fail_or_retry

    def reclaim_after_retry(self, task_id, **kwargs):
        persisted = original(self, task_id, **kwargs)
        if persisted:
            with self._connect() as conn:
                conn.execute(
                    """
                    update tasks set status = ?, claimed_by = ?, artifacts_summary = ?
                    where task_id = ?
                    """,
                    (TaskStatus.RUNNING.value, "replacement-worker", '{"attempt":"replacement"}', task_id),
                )
        return persisted

    monkeypatch.setattr(TaskRepository, "fail_or_retry", reclaim_after_retry)

    run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "failed",
                "error": "network timed out",
                "artifacts": {"attempt": "stale-worker"},
            }
        },
    )

    task = repo.get("retry-reclaimed")
    assert task is not None
    assert task.status == TaskStatus.RUNNING
    assert task.claimed_by == "replacement-worker"
    assert task.artifacts_summary == {"attempt": "replacement"}


def test_executor_persists_skipped_completion_with_its_downstream_ids(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="skipped-reclaimed",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))
    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={
            TaskType.SUMMARY: lambda task, base_dir: {
                "status": "skipped",
                "artifacts": {"attempt": "stale-worker"},
            }
        },
    )

    task = repo.get("skipped-reclaimed")
    assert result["results"][0]["status"] == TaskStatus.SKIPPED.value
    assert task is not None
    assert task.status == TaskStatus.SKIPPED
    assert task.artifacts_summary == {
        "attempt": "stale-worker",
        "downstream_task_ids": [],
    }


def test_executor_keeps_terminal_transition_and_downstream_enqueue_atomic(tmp_path: Path, monkeypatch):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="summary-atomic-reclaim",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))
    replacement = TaskRepository(repo.db_path)
    reclaimed = threading.Event()
    reclaim_errors: list[str] = []
    observed: dict[str, bool] = {}
    from backend.application.tasks import processor_execution

    original = processor_execution.enqueue_downstream

    def reclaim_parent() -> None:
        try:
            replacement.update_status("summary-atomic-reclaim", TaskStatus.PENDING)
            claimed = replacement.claim_task_ids(
                ["summary-atomic-reclaim"],
                worker_id="replacement-worker",
                now="2026-07-10T00:01:00Z",
                lease_expires_at="2026-07-10T00:06:00Z",
            )
            if [task.task_id for task in claimed] != ["summary-atomic-reclaim"]:
                reclaim_errors.append("replacement did not claim the requeued task")
            reclaimed.set()
        except Exception as exc:  # pragma: no cover - asserted after the worker exits
            reclaim_errors.append(str(exc))
            reclaimed.set()

    def reclaim_in_completion_gap(*args, **kwargs):
        thread = threading.Thread(target=reclaim_parent)
        thread.start()
        observed["reclaimed_before_downstream_enqueue"] = reclaimed.wait(timeout=0.2)
        downstream = original(*args, **kwargs)
        observed["thread"] = thread
        return downstream

    monkeypatch.setattr(processor_execution, "enqueue_downstream", reclaim_in_completion_gap)

    run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={
            TaskType.SUMMARY: lambda task, base_dir: {
                "status": "skipped",
                "artifacts": {"attempt": "stale-worker"},
            }
        },
    )

    observed["thread"].join(timeout=1)
    assert reclaim_errors == []
    assert observed["reclaimed_before_downstream_enqueue"] is False


def test_executor_persists_downstream_ids_after_terminal_failure(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="failed-media",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card-1",
        max_attempts=1,
        next_run_at="2026-07-10T00:00:00Z",
    ))

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={
            TaskType.MEDIA_DOWNLOAD: lambda task, base_dir: {
                "status": "failed",
                "error": "permanent media failure",
                "artifacts": {"card_id": "card-1"},
            }
        },
    )

    task = repo.get("failed-media")
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert task.artifacts_summary["downstream_task_ids"] == result["results"][0]["downstream_task_ids"]
    assert task.artifacts_summary["downstream_task_ids"] == []


def test_terminal_failure_preserves_existing_result_metadata(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="failed-media-preserves-metadata",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card-1",
        max_attempts=1,
        next_run_at="2026-07-10T00:00:00Z",
        result_version="previous-result-v1",
    ))

    run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={
            TaskType.MEDIA_DOWNLOAD: lambda task, base_dir: {
                "status": "failed",
                "error": "permanent media failure",
                "artifacts": {"card_id": "card-1"},
            }
        },
    )

    task = repo.get("failed-media-preserves-metadata")
    assert task is not None
    assert task.status == TaskStatus.FAILED
    assert task.result_version == "previous-result-v1"
    assert task.finished_at is None


def test_executor_does_not_defer_a_task_after_its_lease_is_reclaimed(tmp_path: Path, monkeypatch):
    from backend.application.tasks.fetch_rate_limit import FetchThrottleDecision

    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="throttle-reclaimed",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))
    original = TaskRepository.defer

    def reclaim_before_defer(self, task_id, **kwargs):
        with self._connect() as conn:
            conn.execute(
                "update tasks set claimed_by = ?, artifacts_summary = ? where task_id = ?",
                ("replacement-worker", '{"attempt":"replacement"}', task_id),
            )
        return original(self, task_id, **kwargs)

    monkeypatch.setattr(TaskRepository, "defer", reclaim_before_defer)
    monkeypatch.setattr(
        "backend.application.tasks.fetch_rate_limit.reserve_fetch_slot",
        lambda *args, **kwargs: FetchThrottleDecision(
            allowed=False,
            next_run_at="2026-07-10T00:01:00Z",
            throttle_key="source-1",
            reason="min_gap",
        ),
    )

    result = run_due_tasks(tmp_path, now="2026-07-10T00:00:00Z")

    task = repo.get("throttle-reclaimed")
    assert result["results"][0]["error"] == "lease_ownership_lost"
    assert task is not None
    assert task.status == TaskStatus.RUNNING
    assert task.claimed_by == "replacement-worker"
    assert task.artifacts_summary == {"attempt": "replacement"}


def test_executor_recovers_expired_lease_for_every_task_type(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_type in TaskType:
        repo.enqueue(Task(
            task_id=f"expired-{task_type.value}",
            task_type=task_type,
            artifacts_summary={"queue_platform": "xiaohongshu"} if task_type == TaskType.AUTH_QR_CAPTURE else {},
            target_kind="global",
            target_id=task_type.value,
            status=TaskStatus.RUNNING,
            claimed_by="dead-worker",
            lease_expires_at="2026-07-09T23:59:59Z",
        ))

    handlers = {
        task_type: lambda task, base_dir: {"status": "success", "artifacts": {}}
        for task_type in TaskType
    }
    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        limit=len(TaskType),
        handlers=handlers,
    )

    assert result["recovered"] == len(TaskType)
    assert result["succeeded"] == 0
    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:02:00Z",
        limit=len(TaskType),
        handlers=handlers,
    )
    assert result["succeeded"] == len(TaskType)
    assert all(
        repo.get(f"expired-{task_type.value}").status == TaskStatus.SUCCESS
        for task_type in TaskType
    )


def test_transient_failure_sets_future_next_run_at(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="retry-network-timeout",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={TaskType.FETCH: lambda task, base_dir: (_ for _ in ()).throw(RuntimeError("network timed out"))},
    )

    persisted = repo.get("retry-network-timeout")
    assert result["requeued"] == 1
    assert persisted is not None
    assert persisted.status == TaskStatus.PENDING
    assert persisted.attempt == 2
    assert persisted.next_run_at == "2026-07-10T00:00:30Z"
    assert persisted.claimed_by is None


def test_failed_handler_result_uses_retry_policy(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="retry-failed-result",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": "failed",
                "error": "connection timed out",
            }
        },
    )

    persisted = repo.get("retry-failed-result")
    assert result["requeued"] == 1
    assert persisted is not None
    assert persisted.status == TaskStatus.PENDING
    assert persisted.next_run_at == "2026-07-10T00:00:30Z"


def test_auth_failure_becomes_blocked_without_retry(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="auth-blocked",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        next_run_at="2026-07-10T00:00:00Z",
    ))

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T00:00:00Z",
        handlers={TaskType.FETCH: lambda task, base_dir: (_ for _ in ()).throw(RuntimeError("auth failed"))},
    )

    persisted = repo.get("auth-blocked")
    assert result["failed"] == 0
    assert persisted is not None
    assert persisted.status == TaskStatus.BLOCKED
    assert persisted.next_run_at is None
    assert persisted.claimed_by is None


def test_fetch_rate_peek_skips_same_x_identity_before_claim(tmp_path: Path, monkeypatch):
    _isolate_browser_acquisition_batch(monkeypatch)
    source_repo = SourceRepository(tmp_path)
    for source_id in ("x_a", "x_b"):
        source_repo.upsert_source(
            {
                "source_id": source_id,
                "platform": "x",
                "source_type": "creator",
                "identifier": source_id,
                "display_name": source_id,
                "cadence_hours": 0.5,
                "enabled": True,
                "connector_status": "supported",
                "raw_metadata": {
                    "fetch_rate_policy": {
                        "min_gap_seconds": 60,
                        "jitter_seconds": 0,
                        "hourly_budget": 10,
                        "daily_budget": 100,
                    }
                },
            }
        )
    repo = TaskRepository.for_base_dir(tmp_path)
    for source_id in ("x_a", "x_b"):
        repo.enqueue(
            Task(
                task_id=f"fetch_{source_id}",
                task_type=TaskType.FETCH,
                target_kind="source",
                target_id=source_id,
                next_run_at="2026-06-25T00:00:00Z",
            )
        )

    called: list[str] = []

    def fake_fetch(task, base_dir):
        called.append(task.target_id)
        return {"status": "success", "artifacts": {"created_or_updated": "updated"}}

    result = run_due_tasks(
        tmp_path,
        now="2026-06-25T00:00:00Z",
        limit=2,
        max_workers=2,
        handlers={TaskType.FETCH: fake_fetch},
    )

    assert result["succeeded"] == 1
    assert result["requeued"] == 0
    assert len(called) == 1
    deferred = repo.get("fetch_x_b" if called == ["x_a"] else "fetch_x_a")
    assert deferred is not None
    assert deferred.status == TaskStatus.PENDING
    assert deferred.next_run_at == "2026-06-25T00:00:00Z"
    assert deferred.attempt == 1 and deferred.claimed_by is None
    assert TaskEventRepository(tmp_path).list_events(task_id=deferred.task_id) == []
    from backend.application.tasks.processor_throttle import peek_fetch_throttle_decision

    decision = peek_fetch_throttle_decision(deferred, tmp_path, now="2026-06-25T00:00:00Z")
    assert not decision.allowed and decision.next_run_at == "2026-06-25T00:01:00Z"
    assert decision.throttle_key == "x:x_default"


def test_fetch_rate_limit_allows_different_x_identities_in_parallel(tmp_path: Path, monkeypatch):
    _isolate_browser_acquisition_batch(monkeypatch)
    source_repo = SourceRepository(tmp_path)
    source_repo.upsert_source(
        {
            "source_id": "x_default_source",
            "platform": "x",
            "source_type": "creator",
            "identifier": "default_source",
            "display_name": "Default",
            "cadence_hours": 0.5,
            "enabled": True,
            "connector_status": "supported",
            "raw_metadata": {"fetch_rate_policy": {"min_gap_seconds": 60, "jitter_seconds": 0}},
        }
    )
    source_repo.upsert_source(
        {
            "source_id": "x_alt_source",
            "platform": "x",
            "source_type": "creator",
            "identifier": "alt_source",
            "display_name": "Alt",
            "cadence_hours": 0.5,
            "enabled": True,
            "connector_status": "supported",
            "raw_metadata": {
                "fetch_identity_id": "x_alt",
                "fetch_rate_policy": {"min_gap_seconds": 60, "jitter_seconds": 0},
            },
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    for source_id in ("x_default_source", "x_alt_source"):
        repo.enqueue(
            Task(
                task_id=f"fetch_{source_id}",
                task_type=TaskType.FETCH,
                target_kind="source",
                target_id=source_id,
                next_run_at="2026-06-25T00:00:00Z",
            )
        )

    called: list[str] = []
    result = run_due_tasks(
        tmp_path,
        now="2026-06-25T00:00:00Z",
        limit=2,
        max_workers=2,
        handlers={
            TaskType.FETCH: lambda task, base_dir: called.append(task.target_id)
            or {"status": "success", "artifacts": {"created_or_updated": "updated"}}
        },
    )

    assert result["succeeded"] == 2
    assert result["requeued"] == 0
    assert set(called) == {"x_default_source", "x_alt_source"}


def test_fetch_rate_peek_skips_claim_when_hourly_budget_is_exhausted(tmp_path: Path, monkeypatch):
    _isolate_browser_acquisition_batch(monkeypatch)
    source_repo = SourceRepository(tmp_path)
    for source_id in ("x_a", "x_b"):
        source_repo.upsert_source(
            {
                "source_id": source_id,
                "platform": "x",
                "source_type": "creator",
                "identifier": source_id,
                "display_name": source_id,
                "cadence_hours": 0.5,
                "enabled": True,
                "connector_status": "supported",
                "raw_metadata": {
                    "fetch_rate_policy": {
                        "min_gap_seconds": 0,
                        "jitter_seconds": 0,
                        "hourly_budget": 1,
                        "daily_budget": 100,
                    }
                },
            }
        )
    repo = TaskRepository.for_base_dir(tmp_path)
    for source_id in ("x_a", "x_b"):
        repo.enqueue(
            Task(
                task_id=f"budget_{source_id}",
                task_type=TaskType.FETCH,
                target_kind="source",
                target_id=source_id,
                next_run_at="2026-06-25T00:00:00Z",
            )
        )

    called: list[str] = []
    result = run_due_tasks(
        tmp_path,
        now="2026-06-25T00:00:00Z",
        limit=2,
        max_workers=2,
        handlers={
            TaskType.FETCH: lambda task, base_dir: called.append(task.target_id)
            or {"status": "success", "artifacts": {"created_or_updated": "updated"}}
        },
    )

    assert result["succeeded"] == 1
    assert result["requeued"] == 0
    deferred = repo.get("budget_x_b" if called == ["x_a"] else "budget_x_a")
    assert deferred is not None
    assert deferred.next_run_at == "2026-06-25T00:00:00Z"
    assert deferred.attempt == 1 and deferred.claimed_by is None
    assert TaskEventRepository(tmp_path).list_events(task_id=deferred.task_id) == []
    from backend.application.tasks.processor_throttle import peek_fetch_throttle_decision

    decision = peek_fetch_throttle_decision(deferred, tmp_path, now="2026-06-25T00:00:00Z")
    assert not decision.allowed and decision.next_run_at == "2026-06-25T01:00:00Z"
    assert decision.reason == "hourly_budget"
