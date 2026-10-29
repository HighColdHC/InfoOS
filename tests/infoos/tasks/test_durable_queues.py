from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskQueue, TaskStatus, TaskType
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.read_models.task_queue_tracking import (
    queue_summary,
    task_tracking,
    task_tracking_page,
)
from backend.workers.task_worker import run_task_worker_loop


NOW = "2026-07-27T00:00:00Z"


def _source(base_dir: Path, source_id: str, platform: str) -> None:
    SourceRepository(base_dir).upsert_source({
        "source_id": source_id,
        "platform": platform,
        "source_type": "creator" if platform == "x" else "feed",
        "identifier": source_id,
        "display_name": source_id,
        "cadence_hours": 24,
        "enabled": True,
        "connector_status": "supported",
        "connector_id": source_id,
    })


def _card(base_dir: Path, card_id: str, platform: str) -> None:
    CardRepository(base_dir).upsert_card({
        "card_id": card_id,
        "source_platform": platform,
        "source_type": "social_post",
        "source_url": f"https://example.com/{card_id}",
        "title": card_id,
        "content": "content",
        "captured_at": NOW,
        "processed_at": NOW,
        "raw_payload": {},
    })


def test_queue_routing_covers_browser_media_and_all_resource_roles(tmp_path: Path) -> None:
    _source(tmp_path, "rss", "rss")
    _source(tmp_path, "x", "x")
    _card(tmp_path, "x-card", "x")
    _card(tmp_path, "yt-card", "youtube")
    repo = TaskRepository.for_base_dir(tmp_path)
    for task in (
        Task("rss-fetch", TaskType.FETCH, "source", "rss"),
        Task("x-fetch", TaskType.FETCH, "source", "x"),
        Task("x-media", TaskType.MEDIA_DOWNLOAD, "card", "x-card"),
        Task("yt-media", TaskType.MEDIA_DOWNLOAD, "card", "yt-card"),
        Task("asr", TaskType.VIDEO_TRANSCRIBE, "card", "yt-card"),
        Task("card", TaskType.CARD_BUILD, "card", "yt-card"),
        Task("ai", TaskType.CARD_LLM_ENRICH, "card", "yt-card"),
        Task("maint", TaskType.PIPELINE_RECONCILE, "system", "pipeline"),
        Task("archive", TaskType.TASK_EVENT_ARCHIVE, "global", "task_events"),
    ):
        repo.enqueue(task)

    assert repo.get("rss-fetch").queue_name == TaskQueue.PUBLIC_FETCH.value
    assert repo.get("x-fetch").queue_name == TaskQueue.BROWSER_FETCH.value
    assert repo.get("x-media").queue_name == TaskQueue.BROWSER_FETCH.value
    assert repo.get("yt-media").queue_name == TaskQueue.URGENT_MEDIA.value
    assert repo.get("asr").queue_name == TaskQueue.GPU_ASR.value
    assert repo.get("card").queue_name == TaskQueue.CARD_PIPELINE.value
    assert repo.get("ai").queue_name == TaskQueue.MAINTENANCE_IO.value
    assert repo.get("maint").queue_name == TaskQueue.MAINTENANCE_IO.value
    assert repo.get("archive").queue_name == TaskQueue.MAINTENANCE_IO.value


def test_queue_filter_is_atomic_and_due_order_uses_priority(tmp_path: Path) -> None:
    _source(tmp_path, "rss", "rss")
    _card(tmp_path, "yt-card", "youtube")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch", TaskType.FETCH, "source", "rss"))
    repo.enqueue(Task("audio", TaskType.VIDEO_EXTRACT_AUDIO, "card", "yt-card"))
    with sqlite3.connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            """
            update tasks set created_at = '2026-07-27T00:00:00Z',
              deadline_at = '2026-07-27T01:00:00Z'
            where task_id in ('fetch', 'audio')
            """
        )
    repo.enqueue(Task("media", TaskType.MEDIA_DOWNLOAD, "card", "yt-card"))

    assert [
        task.task_id
        for task in repo.list_due(
            now=NOW, queue_names=[TaskQueue.URGENT_MEDIA.value]
        )
    ] == ["media", "audio"]
    assert repo.claim_task_ids(
        ["fetch", "media"],
        worker_id="urgent-worker",
        now=NOW,
        lease_expires_at="2026-07-27T00:05:00Z",
        queue_names=[TaskQueue.URGENT_MEDIA.value],
    )[0].task_id == "media"
    assert repo.get("fetch").claimed_by is None


def test_queue_summary_and_task_tracking_include_worker_and_overdue_wait(tmp_path: Path) -> None:
    _source(tmp_path, "rss", "rss")
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("fetch", TaskType.FETCH, "source", "rss"))
    with sqlite3.connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            """
            update tasks set created_at = '2026-07-27T00:00:00Z',
              deadline_at = '2026-07-27T01:00:00Z'
            where task_id = 'fetch'
            """
        )
    claimed = repo.claim_task_ids(
        ["fetch"],
        worker_id="task-worker-public_fetch-1",
        now="2026-07-27T02:00:00Z",
        lease_expires_at="2026-07-27T02:05:00Z",
        queue_names=[TaskQueue.PUBLIC_FETCH.value],
    )[0]

    summary = queue_summary(tmp_path, evaluated_at="2026-07-27T03:00:00Z")
    assert summary["public_fetch"]["running"] == 1
    assert summary["public_fetch"]["running_worker_ids"] == ["task-worker-public_fetch-1"]
    assert summary["public_fetch"]["running_worker_count"] == 1
    tracking = task_tracking(claimed, evaluated_at="2026-07-27T03:00:00Z")
    assert tracking["queue_name"] == "public_fetch"
    assert tracking["overdue"] is True
    assert tracking["claimed_by"] == "task-worker-public_fetch-1"

    assert repo.complete(
        "fetch",
        worker_id="task-worker-public_fetch-1",
        status=TaskStatus.SUCCESS,
        artifacts={},
        error_reason=None,
        result_version="v1",
        finished_at="2026-07-27T03:00:00Z",
    )
    completed = repo.get("fetch")
    assert completed is not None
    assert task_tracking(
        completed, evaluated_at="2026-07-28T03:00:00Z"
    )["wait_seconds"] == tracking["wait_seconds"]


def test_task_tracking_freezes_unstarted_skipped_wait_at_terminal_time() -> None:
    skipped = Task(
        "skipped",
        TaskType.FETCH,
        "source",
        "rss",
        status=TaskStatus.SKIPPED,
        created_at="2026-07-27T00:00:00Z",
        finished_at="2026-07-27T01:00:00Z",
        updated_at="2026-07-27T01:00:00Z",
    )

    assert task_tracking(
        skipped, evaluated_at="2026-07-28T01:00:00Z"
    )["wait_seconds"] == 3600


def test_worker_loop_passes_queue_filter_and_role(tmp_path: Path) -> None:
    calls = []

    def run_due(base_dir, **kwargs):
        calls.append((base_dir, kwargs))
        return {"processed": 0}

    def stop(stop_event, _seconds):
        stop_event.set()
        return True

    run_task_worker_loop(
        base_dir=tmp_path,
        queue_name=TaskQueue.GPU_ASR.value,
        run_due_tasks_fn=run_due,
        wait_fn=stop,
    )

    assert calls == [(
        tmp_path,
        {
            "limit": 50,
            "max_workers": None,
            "queue_names": ["gpu_asr"],
            "worker_name": "gpu_asr",
        },
    )]


def test_global_task_tracking_page_includes_non_source_tasks(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("export", TaskType.DATA_EXPORT, "export", "backup"))

    page = task_tracking_page(
        tmp_path, queue_name=TaskQueue.MAINTENANCE_IO.value
    )

    assert page["total"] == 1
    assert page["next_offset"] is None
    assert page["items"][0]["task_id"] == "export"
    assert page["items"][0]["queue_name"] == "maintenance_io"


@pytest.mark.parametrize("queue_name", [None, "public_fetch", "card_pipeline", "missing"])
@pytest.mark.parametrize("status", [None, "running", "pending", "success", "failed"])
def test_task_tracking_pages_across_active_and_finished_segments(tmp_path, queue_name, status):
    repo = TaskRepository.for_base_dir(tmp_path)
    # Expected global order: running (priority before recency), pending, then
    # completed tasks by recency and ID, regardless of priority/deadline.
    ordered = [
        ("running-old", "running", "public_fetch", 1, "01"),
        ("running-new", "running", "card_pipeline", 9, "02"),
        ("pending", "pending", "public_fetch", 0, "03"),
        ("finished-a", "success", "public_fetch", 9, "08"),
        ("finished-b", "failed", "card_pipeline", 8, "08"),
        ("finished-c", "success", "public_fetch", 7, "06"),
        ("finished-d", "skipped", "card_pipeline", 6, "05"),
        ("finished-old", "success", "public_fetch", 0, "01"),
    ]
    for task_id, task_status, queue, priority, day in reversed(ordered):
        repo.enqueue(Task(task_id, TaskType.DATA_EXPORT, "export", "backup"))
        with sqlite3.connect(resolve_sqlite_path(tmp_path)) as conn:
            conn.execute(
                """update tasks set status=?, queue_name=?, priority=?,
                       created_at=?, deadline_at=? where task_id=?""",
                (task_status, queue, priority, f"2026-07-{day}T00:00:00Z",
                 f"2026-07-{day}T01:00:00Z", task_id),
            )
    expected = [row[0] for row in ordered
                if (queue_name is None or row[2] == queue_name)
                and (status is None or row[1] == status)]
    actual = []
    offset = 0
    while True:
        page = task_tracking_page(tmp_path, limit=2, offset=offset,
                                  queue_name=queue_name, status=status, evaluated_at=NOW)
        assert page["total"] == len(expected)
        assert [row["task_id"] for row in page["items"]] == expected[offset:offset + 2]
        actual.extend(row["task_id"] for row in page["items"])
        expected_next = offset + 2 if offset + 2 < len(expected) else None
        assert page["next_offset"] == expected_next
        if expected_next is None:
            break
        offset = expected_next
    assert actual == expected
    assert task_tracking_page(tmp_path, offset=100, queue_name=queue_name, status=status)["items"] == []
