from __future__ import annotations

from pathlib import Path

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.application.tasks.registry import RETIRED_TASK_TYPES, handler_for
from backend.application.tasks.worker import run_due_tasks, run_tasks_by_ids


def test_worker_accepts_custom_handler_override(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_1",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_1",
        )
    )

    result = run_tasks_by_ids(
        tmp_path,
        ["fetch_source_1"],
        handlers={
            TaskType.FETCH: lambda task, base_dir: {
                "status": TaskStatus.SUCCESS.value,
                "artifacts": {"handler": "custom"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert repo.get("fetch_source_1").artifacts_summary["handler"] == "custom"


def test_worker_claims_pending_child_when_blocked_dependency_is_required(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="media_blocked",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_1",
        status=TaskStatus.BLOCKED,
    ))
    repo.enqueue(Task(
        task_id="context_after_blocked_media",
        task_type=TaskType.CONTEXT_PACK,
        target_kind="card",
        target_id="card_1",
        depends_on=(DependencySpec("media_blocked", TaskStatus.BLOCKED),),
    ))

    result = run_due_tasks(
        tmp_path,
        now="2026-07-10T01:00:00Z",
        handlers={
            TaskType.CONTEXT_PACK: lambda task, base_dir: {
                "status": TaskStatus.SUCCESS.value,
                "artifacts": {"handler": "custom"},
            }
        },
    )

    assert result["succeeded"] == 1
    assert repo.get("context_after_blocked_media").status == TaskStatus.SUCCESS


def test_active_task_types_have_handlers_and_retired_tasks_skip_safely(tmp_path: Path):
    for task_type in list(TaskType):
        handler = handler_for(task_type)

        assert callable(handler)
        if task_type in RETIRED_TASK_TYPES:
            result = handler(Task(task_id=f"retired_{task_type.value}", task_type=task_type, target_kind="card", target_id="card_1"), tmp_path)
            assert result == {"status": "skipped", "artifacts": {"reason": "feature_retired"}}
        else:
            assert handler.__name__ != "<lambda>"
