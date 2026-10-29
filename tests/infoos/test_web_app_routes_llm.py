"""Web app route tests — run-once, scheduler and browser session preparation."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.main import create_app
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.planner import fetch_task_id
from backend.infrastructure.storage.repositories import SourceRepository


def test_run_configurable_source_api_returns_async_task(tmp_path):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "source_a",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Source A",
            "cadence_hours": 12,
            "enabled": True,
            "connector_status": "supported",
        }
    )
    client = TestClient(create_app(base_dir=tmp_path))

    response = client.post("/api/configurable-sources/source_a/run", json={})

    assert response.status_code == 202
    payload = response.json()
    assert payload["ok"] is True
    assert payload["batch"]["batch_id"].startswith("source_run_")
    assert payload["batch"]["task_ids"]
    assert payload["batch"]["progress_url"].startswith("/api/task-batches/")

def test_run_configurable_source_api_runs_one_database_source(tmp_path, monkeypatch):
    app = create_app(base_dir=tmp_path)
    client = TestClient(app)
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "RSS AI",
            "cadence_hours": 12,
            "enabled": True,
            "connector_status": "supported",
        }
    )

    response = client.post("/api/configurable-sources/rss_ai/run", json={"max_attempts": 3})

    assert response.status_code == 202
    assert response.json()["ok"] is True
    assert response.json()["batch"]["source_ids"] == ["rss_ai"]

def test_run_configurable_source_api_creates_fresh_manual_task_beside_due_fetch(tmp_path, monkeypatch):
    from backend.application.tasks.planner import fetch_task_id
    from backend.application.tasks.repository import TaskRepository
    from backend.application.tasks.types import Task, TaskStatus, TaskType

    app = create_app(base_dir=tmp_path)
    client = TestClient(app)
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "x_ai",
            "platform": "x",
            "source_type": "creator",
            "identifier": "elonmusk",
            "display_name": "Elon Musk",
            "cadence_hours": 24.0,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "x_ai",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    source = SourceRepository(tmp_path).get_source("x_ai")
    version = source["calibration_input_version"]
    due_fetch_id = fetch_task_id("x_ai", "2026-06-23T00:00:00Z", input_version=version)
    repo.enqueue(
        Task(
            task_id=due_fetch_id,
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="x_ai",
            next_run_at="2026-06-23T00:00:00Z",
            input_version=version,
            artifacts_summary={"source_input_version": version},
        )
    )

    def fake_now():
        return "2026-06-23T00:01:00Z"

    monkeypatch.setattr("backend.application.tasks.planner.now_utc_iso", fake_now)
    monkeypatch.setattr("backend.api.routes.source_runs.now_utc_iso", fake_now)

    first = client.post("/api/configurable-sources/x_ai/run")
    second = client.post("/api/configurable-sources/x_ai/run")

    assert first.status_code == second.status_code == 202
    first_id = first.json()["batch"]["task_ids"][0]
    second_id = second.json()["batch"]["task_ids"][0]
    assert first_id != due_fetch_id
    assert second_id != due_fetch_id
    assert first_id != second_id
    assert repo.get(first_id).artifacts_summary["manual_run"] is True
    assert repo.get(second_id).artifacts_summary["manual_run"] is True
    assert repo.get(first_id).artifacts_summary["chain_id"] != repo.get(second_id).artifacts_summary["chain_id"]
    assert repo.get(due_fetch_id).status == TaskStatus.PENDING

def test_run_configurable_source_api_returns_async_batch_for_retry_click(tmp_path):
    app = create_app(base_dir=tmp_path)
    client = TestClient(app)
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "x_ai",
            "platform": "x",
            "source_type": "creator",
            "identifier": "elonmusk",
            "display_name": "Elon Musk",
            "cadence_hours": 24.0,
            "enabled": True,
            "connector_status": "supported",
        }
    )

    response = client.post("/api/configurable-sources/x_ai/run")

    assert response.status_code == 202
    assert response.json()["ok"] is True
    assert response.json()["batch"]["batch_id"].startswith("source_run_")
