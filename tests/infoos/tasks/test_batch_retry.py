from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.batches import create_task_batch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.usecases.task_batch_retry import retry_task_batch


def _failed_batch(base_dir):
    repo = TaskRepository.for_base_dir(base_dir)
    repo.enqueue(
        Task(
            task_id="fetch_failed",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_failed",
        )
    )
    repo.enqueue(
        Task(
            task_id="fetch_pending",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_pending",
        )
    )
    repo.update_status("fetch_failed", TaskStatus.FAILED, error_reason="timeout")
    return create_task_batch(
        base_dir,
        batch_type="source_run",
        task_ids=["fetch_failed", "fetch_pending"],
    )


def test_batch_retry_is_an_application_use_case_and_read_model_is_read_only(tmp_path):
    batch = _failed_batch(tmp_path)

    retried = retry_task_batch(tmp_path, batch["batch_id"])

    assert retried["batch_id"] == batch["batch_id"]
    assert retried["retried_task_ids"] == ["fetch_failed"]
    assert TaskRepository.for_base_dir(tmp_path).get("fetch_failed").status == TaskStatus.PENDING


def test_task_batch_retry_route_preserves_response_contract(tmp_path):
    batch = _failed_batch(tmp_path)

    response = TestClient(create_app(tmp_path)).post(
        f"/api/task-batches/{batch['batch_id']}/retry"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["batch"]["batch_id"] == batch["batch_id"]
    assert payload["batch"]["retried_task_ids"] == ["fetch_failed"]


def test_task_batch_retry_route_preserves_missing_batch_response(tmp_path):
    response = TestClient(create_app(tmp_path)).post(
        "/api/task-batches/missing/retry"
    )

    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": "batch not found"}
