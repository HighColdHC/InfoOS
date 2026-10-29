from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks


pytestmark = pytest.mark.fault


def test_worker_process_crash_recovers_expired_lease_and_completes_once(tmp_path: Path) -> None:
    """A killed worker leaves only a recoverable lease, never a duplicate completion."""
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="crash-recovery",
            task_type=TaskType.CONTENT_ENRICH,
            target_kind="card",
            target_id="card-crash",
            next_run_at="2026-07-11T00:00:00Z",
        )
    )
    script = """
from backend.application.tasks.repository import TaskRepository
repo = TaskRepository.for_base_dir(r'''%s''')
claimed = repo.claim_task_ids(
    ['crash-recovery'],
    worker_id='crashed-worker', now='2026-07-11T00:00:00Z',
    lease_expires_at='2026-07-11T00:00:10Z',
)
assert [task.task_id for task in claimed] == ['crash-recovery']
import os
os._exit(9)
""" % tmp_path
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3])}
    crashed = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[3], env=env)

    assert crashed.returncode == 9
    calls: list[str] = []
    result = run_due_tasks(
        tmp_path,
        now="2026-07-11T00:00:11Z",
        handlers={
            TaskType.CONTENT_ENRICH: lambda task, _base_dir: calls.append(task.task_id) or {
                "status": "success", "artifacts": {"recovered": True}
            }
        },
    )

    assert result["recovered"] == 1
    assert result["succeeded"] == 0
    assert repo.get("crash-recovery").attempt == 2
    assert repo.get("crash-recovery").next_run_at == "2026-07-11T00:02:11Z"
    result = run_due_tasks(
        tmp_path,
        now="2026-07-11T00:02:11Z",
        handlers={
            TaskType.CONTENT_ENRICH: lambda task, _base_dir: calls.append(task.task_id) or {
                "status": "success", "artifacts": {"recovered": True}
            }
        },
    )
    assert result["succeeded"] == 1
    assert calls == ["crash-recovery"]
    assert repo.get("crash-recovery").status == TaskStatus.SUCCESS
