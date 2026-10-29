from __future__ import annotations

from backend.application.tasks.processor import process_task
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.failures import OpenCLISiteLeaseTimeout


def _claimed(tmp_path) -> tuple[TaskRepository, Task]:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("task", TaskType.FETCH, "source", "source"))
    task = repo.claim_task_ids(
        ["task"],
        worker_id="worker",
        now="2026-07-23T00:00:00Z",
        lease_expires_at="2026-07-23T00:05:00Z",
    )[0]
    return repo, task


def test_explicit_site_timeout_defers_without_attempt(tmp_path) -> None:
    repo, task = _claimed(tmp_path)

    result = process_task(
        task,
        tmp_path,
        now="2026-07-23T00:00:00Z",
        worker_id="worker",
        lease_seconds=300,
        handlers={
            TaskType.FETCH: lambda *_: (_ for _ in ()).throw(
                OpenCLISiteLeaseTimeout("twitter")
            )
        },
    )

    durable = repo.get(task.task_id)
    assert result["status"] == "pending"
    assert durable is not None and durable.status is TaskStatus.PENDING
    assert durable.attempt == 1
    assert durable.last_failure_class is None


def test_other_timeout_is_a_real_failure(tmp_path) -> None:
    repo, task = _claimed(tmp_path)

    result = process_task(
        task,
        tmp_path,
        now="2026-07-23T00:00:00Z",
        worker_id="worker",
        lease_seconds=300,
        handlers={
            TaskType.FETCH: lambda *_: (_ for _ in ()).throw(
                TimeoutError("ordinary network timeout")
            )
        },
    )

    durable = repo.get(task.task_id)
    assert result["status"] == "pending"
    assert durable is not None and durable.attempt == 2
    assert durable.last_failure_class == "network_timeout"
