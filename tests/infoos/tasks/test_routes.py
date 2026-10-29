from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.app.main import create_app


def test_tasks_api_lists_and_retries_failed_tasks(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="video_transcribe_asset_1",
            task_type=TaskType.VIDEO_TRANSCRIBE,
            target_kind="asset",
            target_id="asset_1",
            artifacts_summary={"audio_path": "/tmp/a.wav"},
        )
    )
    repo.update_status("video_transcribe_asset_1", TaskStatus.FAILED, error_reason="timeout")

    client = TestClient(create_app(tmp_path))
    listed = client.get("/api/tasks", params={"task_type": "video_transcribe"}).json()["tasks"]
    retried = client.post("/api/tasks/video_transcribe_asset_1/retry")

    assert listed[0]["task_id"] == "video_transcribe_asset_1"
    assert listed[0]["artifacts_summary"] == {"audio_path": "/tmp/a.wav"}
    assert retried.status_code == 200
    assert retried.json()["task"]["status"] == "pending"
    assert retried.json()["task"]["error_reason"] is None
    assert retried.json()["task"]["attempt"] == 2


def test_task_manager_filters_by_chain_id(tmp_path: Path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_a_chain_a",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
            artifacts_summary={"chain_id": "chain_a", "chain_root_task_id": "fetch_source_a_chain_a"},
        )
    )
    repo.enqueue(
        Task(
            task_id="fetch_source_a_chain_b",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
            artifacts_summary={"chain_id": "chain_b", "chain_root_task_id": "fetch_source_a_chain_b"},
        )
    )

    response = TestClient(create_app(tmp_path)).get("/api/tasks", params={"chain_id": "chain_a"})

    assert response.status_code == 200
    assert [task["task_id"] for task in response.json()["tasks"]] == ["fetch_source_a_chain_a"]
