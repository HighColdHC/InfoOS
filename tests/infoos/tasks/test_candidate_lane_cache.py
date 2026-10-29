from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
import threading

import pytest

from backend.application.tasks import candidate_lane_cache, continuous_executor, lanes
from backend.application.tasks.candidate_lane_cache import CandidateLaneCache
from backend.application.tasks.resource_governor import TaskResourcePlan
from backend.application.tasks.types import Task, TaskType
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository


def _count_reads(monkeypatch, kind, platform):
    reads = Counter()

    def source(_self, source_id):
        reads[source_id] += 1
        return {"source_id": source_id, "platform": platform,
                "source_type": "feed" if platform == "rss" else "creator",
                "identifier": "https://example.com/feed" if platform == "rss" else "example"}

    def card_platform(_self, card_id):
        reads[card_id] += 1
        return platform

    def full_card(*_args, **_kwargs):
        pytest.fail("lane lookup must not load or decode a full card")

    monkeypatch.setattr(SourceRepository, "get_source", source)
    monkeypatch.setattr(CardRepository, "get_card_platform", card_platform)
    monkeypatch.setattr(CardRepository, "get_card", full_card)
    task_type = TaskType.FETCH if kind == "source" else TaskType.CONTENT_ENRICH
    return reads, Task("candidate", task_type, kind, "candidate")


@pytest.mark.parametrize("kind,platform", [("source", "rss"), ("source", "x"), ("card", "x")])
def test_lane_resolution_reads_only_once(tmp_path, monkeypatch, kind, platform):
    reads, task = _count_reads(monkeypatch, kind, platform)
    lanes.execution_lane_for(task, tmp_path)
    assert reads == {"candidate": 1}


@pytest.mark.parametrize("kind,platform", [("source", "rss"), ("card", "x")])
def test_cache_reuses_lookup_until_ttl_and_keys_by_input_and_attempt(tmp_path, monkeypatch, kind, platform):
    reads, task = _count_reads(monkeypatch, kind, platform)
    clock = [100.0]
    monkeypatch.setattr(candidate_lane_cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    cache = CandidateLaneCache(tmp_path)
    expected = cache.get(task)
    clock[0] = 159.999
    assert cache.get(replace(task)) == expected
    assert reads == {"candidate": 1}
    clock[0] = 160.0
    assert cache.get(task) == expected
    assert reads == {"candidate": 2}
    cache.get(replace(task, input_version="changed"))
    cache.get(replace(task, attempt=task.attempt + 1))
    assert reads == {"candidate": 4}


def test_cache_clears_at_capacity(tmp_path, monkeypatch):
    reads, task = _count_reads(monkeypatch, "card", "x")
    cache = CandidateLaneCache(tmp_path)
    for index in range(5001):
        cache.get(replace(task, task_id=str(index)))
    cache.get(replace(task, task_id="5000"))
    assert reads == {"candidate": 5001}
    cache.get(replace(task, task_id="0"))
    assert reads == {"candidate": 5002}


@pytest.mark.parametrize("kind,platform", [("source", "rss"), ("source", "x"), ("card", "x")])
def test_repeated_full_lane_refill_reads_candidate_once(tmp_path, monkeypatch, kind, platform):
    reads, candidate = _count_reads(monkeypatch, kind, platform)
    active = replace(candidate, task_id="active", target_id="active")
    stop, release = threading.Event(), threading.Event()
    pages = []
    live_checks = []

    def due_page(**_kwargs):
        pages.append(1)
        if len(pages) == 4:
            stop.set()
            release.set()
            return [], None
        return ([active, candidate] if len(pages) == 1 else [replace(candidate)]), None

    def execute(task, **_kwargs):
        assert task.task_id == "active"
        assert release.wait(5)
        return {"status": "success"}, None

    repo = SimpleNamespace(
        recover_expired_leases=lambda *_args, **_kwargs: 0,
        list_due_page=due_page,
        claim_task_ids=lambda *_args, **_kwargs: [active],
        claim_task_ids_with_resources=lambda *_args, **_kwargs: [SimpleNamespace(task=active, resource=None)],
    )
    monkeypatch.setattr(continuous_executor.TaskRepository, "for_base_dir", lambda _: repo)
    monkeypatch.setattr(continuous_executor, "_execute_claimed", execute)
    monkeypatch.setattr(continuous_executor, "public_fetch_worker_count", lambda: 1)
    monkeypatch.setattr(continuous_executor, "browser_fetch_worker_count", lambda: 1)
    monkeypatch.setattr(continuous_executor, "resource_plan_for", lambda _: TaskResourcePlan(allowed=True))

    def throttle(task, *_args, **_kwargs):
        live_checks.append(("throttle", task.task_id))
        return SimpleNamespace(allowed=True)

    def checkpoint(_base, task):
        live_checks.append(("checkpoint", task.task_id))
        return False

    monkeypatch.setattr(continuous_executor, "peek_fetch_throttle_decision", throttle)
    monkeypatch.setattr(continuous_executor, "complete_checkpoint", checkpoint)
    browser = SimpleNamespace(close_if_idle=lambda **_kwargs: None)
    try:
        assert continuous_executor.run_continuous_tasks(
            tmp_path, queue_name="test", stop_event=stop, max_workers=1,
            poll_seconds=0.001, browser_lifetime=browser,
        ) == 1
    finally:
        release.set()
        stop.set()
    assert len(pages) == 4
    assert reads == {"active": 1, "candidate": 1}
    assert all(task_id == "active" for _, task_id in live_checks)
    if platform == "x":
        assert live_checks == [("throttle", "active"), ("checkpoint", "active")]
