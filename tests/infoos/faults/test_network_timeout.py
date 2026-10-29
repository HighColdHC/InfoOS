from __future__ import annotations

from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import RawItemRepository, SourceCursorRepository

from tests.infoos.faults.helpers import fetch_task, raw_item, seed_source


pytestmark = pytest.mark.fault


def test_network_timeout_retries_without_advancing_cursor_or_duplicate_raw_items(tmp_path: Path, monkeypatch) -> None:
    source_id = "timeout-source"
    seed_source(tmp_path, source_id=source_id)
    task = fetch_task(source_id)
    TaskRepository.for_base_dir(tmp_path).enqueue(task)

    class TimeoutThenSuccessConnector:
        connector_id = source_id

        def __init__(self) -> None:
            self.calls = 0

        def fetch_new_items(self, since_cursor):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("connection timed out")
            return FetchResult(items=[raw_item(source_id)], next_cursor="cursor-1", visible_count=1)

    connector = TimeoutThenSuccessConnector()
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_args: connector)

    first = run_due_tasks(tmp_path, now="2026-07-11T00:00:00Z", task_types=[TaskType.FETCH])
    deferred = TaskRepository.for_base_dir(tmp_path).get(task.task_id)

    assert first["requeued"] == 1
    assert deferred.status == TaskStatus.PENDING
    assert deferred.next_run_at == "2026-07-11T00:00:30Z"
    assert SourceCursorRepository(tmp_path).get(source_id) is None
    assert RawItemRepository(tmp_path).get("remote-1") is None

    second = run_due_tasks(tmp_path, now="2026-07-11T00:00:31Z", task_types=[TaskType.FETCH])

    assert second["succeeded"] == 1
    assert connector.calls == 2
    assert SourceCursorRepository(tmp_path).get(source_id)["cursor_value"] == "cursor-1"
    assert RawItemRepository(tmp_path).get("remote-1") is not None
    assert len(TaskRepository.for_base_dir(tmp_path).list_by_target(target_kind="source", target_id=source_id)) == 1
