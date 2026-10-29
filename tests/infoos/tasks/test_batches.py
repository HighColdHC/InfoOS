from __future__ import annotations

from backend.application.tasks.batches import (
    attach_task_to_parent_batches as _attach_task_to_parent_batches,
    create_task_batch,
    get_task_batch,
    list_task_batches,
)
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.usecases import task_submission
from backend.infrastructure.storage.database import connect
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.task_queries import TaskReadQuery
from backend.read_models.task_batches import read_task_batch

import pytest


def test_create_and_read_task_batch(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_a",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
        )
    )

    batch = create_task_batch(
        tmp_path,
        batch_type="source_run",
        task_ids=["fetch_source_a"],
        source_ids=["source_a"],
        created_at="2026-06-30T01:00:00Z",
    )

    assert batch["batch_id"].startswith("source_run_")
    loaded = read_task_batch(tmp_path, batch["batch_id"])
    assert loaded["batch_type"] == "source_run"
    assert loaded["task_ids"] == ["fetch_source_a"]
    assert loaded["source_ids"] == ["source_a"]
    assert loaded["summary"]["planned"] == 1
    assert [task["task_id"] for task in loaded["tasks"]] == ["fetch_source_a"]
    assert loaded["tasks"][0]["display_stage"] == "fetch"
    assert loaded["tasks"][0]["source_id"] == "source_a"
    assert "recovery" in loaded["tasks"][0]
    assert "product_artifacts" not in loaded["tasks"][0]
    assert list_task_batches(tmp_path)[0]["batch_id"] == batch["batch_id"]


def test_submit_task_batch_persists_root_and_batch_atomically(tmp_path):
    root = Task(
        task_id="fetch_atomic",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source_atomic",
    )

    batch = task_submission.submit_task_batch(
        tmp_path,
        batch_type="source_run",
        root_tasks=[root],
        source_ids=["source_atomic"],
    )

    stored = TaskRepository.for_base_dir(tmp_path).get(root.task_id)
    assert stored is not None
    assert stored.task_id == root.task_id
    assert stored.task_type == root.task_type
    assert stored.queue_name == "public_fetch"
    assert batch["task_ids"] == [root.task_id]


def test_submit_task_batch_rolls_back_root_when_batch_item_write_fails(tmp_path, monkeypatch):
    root = Task(
        task_id="fetch_rollback",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source_rollback",
    )

    def fail_batch_items(*args, **kwargs):
        raise RuntimeError("batch item write failed")

    monkeypatch.setattr(
        "backend.infrastructure.storage.tasks.batches._insert_batch_items",
        fail_batch_items,
    )

    with pytest.raises(RuntimeError, match="batch item write failed"):
        task_submission.submit_task_batch(tmp_path, batch_type="source_run", root_tasks=[root])

    assert TaskRepository.for_base_dir(tmp_path).get(root.task_id) is None
    with connect(resolve_sqlite_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_batches").fetchone()[0] == 0


def test_submit_task_batch_updates_existing_root_artifacts_in_same_transaction(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    existing = Task(
        task_id="fetch_setup_existing",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source_setup",
        artifacts_summary={"source_id": "source_setup"},
    )
    repo.enqueue(existing)

    task_submission.submit_task_batch(
        tmp_path,
        batch_type="setup_first_fetch",
        existing_task_ids=[existing.task_id],
        task_artifact_updates={existing.task_id: {"setup_first_fetch": True}},
    )

    assert repo.get(existing.task_id).artifacts_summary == {
        "source_id": "source_setup",
        "setup_first_fetch": True,
    }


def test_submit_task_batch_rejects_missing_existing_task_without_empty_batch(tmp_path):
    with pytest.raises(ValueError, match="missing task ids: fetch_missing"):
        task_submission.submit_task_batch(
            tmp_path,
            batch_type="source_run",
            existing_task_ids=["fetch_missing"],
        )

    with connect(resolve_sqlite_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_batches").fetchone()[0] == 0


def test_submit_task_batch_rejects_empty_submission(tmp_path):
    with pytest.raises(ValueError, match="at least one root task"):
        task_submission.submit_task_batch(tmp_path, batch_type="source_run")

    with connect(resolve_sqlite_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from task_batches").fetchone()[0] == 0


def test_same_child_is_not_attached_twice(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    for task_id, task_type in (
        ("fetch_source_a", TaskType.FETCH),
        ("card_build_raw_a", TaskType.CARD_BUILD),
    ):
        repo.enqueue(
            Task(
                task_id=task_id,
                task_type=task_type,
                target_kind="source" if task_type == TaskType.FETCH else "raw_item",
                target_id="source_a" if task_type == TaskType.FETCH else "raw_a",
            )
        )

    batch = create_task_batch(tmp_path, batch_type="source_run", task_ids=["fetch_source_a"])

    assert _attach_task_to_parent_batches(
        tmp_path,
        parent_task_id="fetch_source_a",
        child_task_ids=["card_build_raw_a"],
    ) == 1
    assert _attach_task_to_parent_batches(
        tmp_path,
        parent_task_id="fetch_source_a",
        child_task_ids=["card_build_raw_a"],
    ) == 0
    assert get_task_batch(tmp_path, batch["batch_id"])["task_ids"] == [
        "fetch_source_a",
        "card_build_raw_a",
    ]


def test_batch_summary_includes_downstream_tasks(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_a",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
            status=TaskStatus.SUCCESS,
        )
    )
    repo.enqueue(
        Task(
            task_id="card_build_raw_a",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="raw_a",
            status=TaskStatus.PENDING,
        )
    )
    batch = create_task_batch(tmp_path, batch_type="source_run", task_ids=["fetch_source_a"])
    _attach_task_to_parent_batches(
        tmp_path,
        parent_task_id="fetch_source_a",
        child_task_ids=["card_build_raw_a"],
    )

    loaded = get_task_batch(tmp_path, batch["batch_id"])

    assert loaded["summary"]["total"] == 2
    assert loaded["summary"]["terminal"] == 1
    assert loaded["summary"]["progress_percent"] == 50
    assert loaded["summary"]["current_stage"] == TaskType.CARD_BUILD.value
    assert loaded["summary"]["oldest_pending_at"] is not None


def test_batch_remains_running_while_descendant_is_pending(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_a",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
            status=TaskStatus.RUNNING,
        )
    )
    repo.enqueue(
        Task(
            task_id="card_build_raw_a",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="raw_a",
        )
    )
    batch = create_task_batch(tmp_path, batch_type="source_run", task_ids=["fetch_source_a"])
    _attach_task_to_parent_batches(
        tmp_path,
        parent_task_id="fetch_source_a",
        child_task_ids=["card_build_raw_a"],
    )

    assert get_task_batch(tmp_path, batch["batch_id"])["status"] == "running"


def test_batch_finishes_only_after_all_descendants_terminal(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_a",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
            status=TaskStatus.SUCCESS,
        )
    )
    repo.enqueue(
        Task(
            task_id="card_build_raw_a",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id="raw_a",
        )
    )
    batch = create_task_batch(tmp_path, batch_type="source_run", task_ids=["fetch_source_a"])
    _attach_task_to_parent_batches(
        tmp_path,
        parent_task_id="fetch_source_a",
        child_task_ids=["card_build_raw_a"],
    )

    assert get_task_batch(tmp_path, batch["batch_id"])["status"] == "queued"

    repo.update_status("card_build_raw_a", TaskStatus.FAILED)
    assert get_task_batch(tmp_path, batch["batch_id"])["status"] == "partial"

    repo.update_status("card_build_raw_a", TaskStatus.SUCCESS)
    loaded = get_task_batch(tmp_path, batch["batch_id"])

    assert loaded["status"] == "success"
    assert loaded["summary"]["terminal"] == 2
    assert loaded["summary"]["progress_percent"] == 100


def test_batch_projection_matches_previous_per_task_summary_across_sql_chunks(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    statuses = [TaskStatus.SUCCESS] * 400 + [
        TaskStatus.RUNNING, TaskStatus.PENDING, TaskStatus.FAILED,
    ]
    tasks = [
        Task(f"batch_equivalent_{index:03}", TaskType.CARD_BUILD, "raw_item", str(index),
             status=status, artifacts_summary={"created_or_updated": "created" if index % 2 == 0 else "updated"})
        for index, status in enumerate(statuses)
    ]
    repo.enqueue_many(tasks)
    task_ids = [task.task_id for task in tasks]
    batch = create_task_batch(tmp_path, batch_type="source_run", task_ids=task_ids)
    projected = get_task_batch(tmp_path, batch["batch_id"])
    read_query = TaskReadQuery(tmp_path)
    previous_tasks = [read_query.get(task_id) for task_id in task_ids]
    assert all(task is not None for task in previous_tasks)
    terminal = sum(task.status in {TaskStatus.SUCCESS, TaskStatus.FAILED,
                                   TaskStatus.BLOCKED, TaskStatus.SKIPPED}
                   for task in previous_tasks)
    pending_times = [task.created_at for task in previous_tasks
                     if task.status == TaskStatus.PENDING]
    expected = {
        "planned": len(task_ids), "total": len(task_ids), "terminal": terminal,
        "progress_percent": int(terminal * 100 / len(task_ids)),
        "current_stage": TaskType.CARD_BUILD.value,
        "oldest_pending_at": min(pending_times),
        "running": 1, "succeeded": 400, "failed": 1,
        "blocked": 0, "skipped": 0, "pending": 1,
        "new_cards": sum(task.task_type == TaskType.CARD_BUILD
                         and task.artifacts_summary.get("created_or_updated") == "created"
                         for task in previous_tasks),
    }
    assert projected["task_ids"] == task_ids
    assert projected["summary"] == expected
    assert projected["status"] == "running"
    assert list_task_batches(tmp_path, limit=1) == [projected]
