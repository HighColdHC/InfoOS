from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.api.routes.collector_status import router
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.repositories import RunRepository, SourceRepository
from tests.infoos._factories import make_source_payload


def _client(base_dir) -> TestClient:
    initialize_runtime_storage(base_dir)
    app = FastAPI()
    app.state.base_dir = base_dir
    app.include_router(router)
    return TestClient(app)


def test_collector_status_is_paused_without_configured_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.read_models.collector_status.read_container_health", lambda: {"status": "unknown"})
    response = _client(tmp_path).get("/api/collector-status")

    assert response.status_code == 200
    assert response.json() == {
        "service_status": "paused",
        "latest_collection_at": None,
        "collection_status": "paused",
        "enabled_sources": 0,
        "total_sources": 0,
        "overdue_sources": 0,
        "queue": {},
        "providers": [],
        "runtime_status": "unknown",
        "reasons": [],
    }


def test_collector_status_is_registered_on_the_product_app(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.read_models.collector_status.read_container_health", lambda: {"status": "healthy"})
    response = TestClient(create_app(tmp_path)).get("/api/collector-status")

    assert response.status_code == 200
    assert response.json() == {
        "service_status": "paused", "latest_collection_at": None,
        "collection_status": "paused", "enabled_sources": 0, "total_sources": 0,
        "overdue_sources": 0, "queue": {}, "providers": [],
        "runtime_status": "healthy", "reasons": [],
    }


def test_collector_status_uses_latest_successful_fetch_completion(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("backend.read_models.collector_status.read_container_health", lambda: {"status": "healthy"})
    client = _client(tmp_path)
    SourceRepository(tmp_path).upsert_source(
        make_source_payload(
            source_id="rss-source",
            connector_id="rss-source",
            identifier="https://example.test/feed.xml",
        )
    )
    runs = RunRepository(tmp_path)
    runs.append_run(
        {
            "run_id": "fetch-run",
            "source_id": "rss-source",
            "scheduled_for": "2026-07-16T09:59:00Z",
            "started_at": "2026-07-16T10:00:00Z",
            "finished_at": "2026-07-16T10:01:00Z",
            "status": "success",
            "task_type": "fetch",
        }
    )
    runs.append_run(
        {
            "run_id": "newer-non-fetch-run",
            "source_id": "rss-source",
            "scheduled_for": "2026-07-16T11:59:00Z",
            "started_at": "2026-07-16T12:00:00Z",
            "finished_at": "2026-07-16T12:01:00Z",
            "status": "success",
            "task_type": "card_build",
        }
    )
    tasks = TaskRepository.for_base_dir(tmp_path)
    tasks.enqueue(
        Task(
            task_id="started-fetch-task",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss-source",
            status=TaskStatus.RUNNING,
            started_at="2026-07-16T11:00:00Z",
        )
    )
    tasks.enqueue(
        Task(
            task_id="unstarted-fetch-task",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss-source",
        )
    )

    response = client.get("/api/collector-status")

    assert response.status_code == 200
    assert response.json() == {
        "service_status": "ready",
        "latest_collection_at": "2026-07-16T10:01:00Z",
        "collection_status": "active",
        "enabled_sources": 1,
        "total_sources": 1,
        "overdue_sources": 0,
        "queue": {"pending": 1, "running": 1},
        "providers": [],
        "runtime_status": "healthy",
        "reasons": [],
    }
