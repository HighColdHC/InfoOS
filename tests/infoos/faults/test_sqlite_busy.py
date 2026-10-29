from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks


pytestmark = pytest.mark.fault


def test_sqlite_busy_claim_waits_without_partial_rows_or_duplicate_owner(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("busy-claim", TaskType.CONTENT_ENRICH, "card", "busy-card"))
    lock = sqlite3.connect(repo.db_path, timeout=1, isolation_level=None)
    lock.execute("begin immediate")
    started = threading.Event()
    outcome: dict[str, object] = {}

    def worker() -> None:
        started.set()
        outcome["result"] = run_due_tasks(
            tmp_path,
            handlers={TaskType.CONTENT_ENRICH: lambda *_args: {"status": "success", "artifacts": {}}},
        )

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(timeout=1)
    time.sleep(0.1)
    assert repo.get("busy-claim").status == TaskStatus.PENDING
    assert "result" not in outcome
    lock.execute("commit")
    lock.close()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcome["result"]["succeeded"] == 1
    stored = repo.get("busy-claim")
    assert stored.status == TaskStatus.SUCCESS
    assert stored.claimed_by is None
    with sqlite3.connect(repo.db_path) as conn:
        assert conn.execute("select count(*) from tasks where task_id = 'busy-claim'").fetchone()[0] == 1
