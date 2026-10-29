"""The archive task runs through the existing continuous maintenance lane."""
from __future__ import annotations

from datetime import datetime, timezone
import threading

from backend.application.tasks.continuous_executor import run_continuous_tasks
from backend.application.tasks.handlers.task_event_archive import handle_task_event_archive
from backend.application.tasks.planner import enqueue_task_event_archive_task
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType


def test_archive_task_executes_in_continuous_maintenance_lane(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    task_id = enqueue_task_event_archive_task(tmp_path, now=now)
    stop = threading.Event()

    def finish_and_stop(task, base_dir):
        result = handle_task_event_archive(task, base_dir)
        stop.set()
        return result

    processed = run_continuous_tasks(
        tmp_path,
        queue_name="maintenance_io",
        stop_event=stop,
        poll_seconds=0.01,
        batch_limit=1,
        max_workers=1,
        handlers={TaskType.TASK_EVENT_ARCHIVE: finish_and_stop},
    )
    task = TaskRepository.for_base_dir(tmp_path).get(task_id)
    assert processed == 1
    assert task.status is TaskStatus.SUCCESS
    assert "history_stats" in task.artifacts_summary
