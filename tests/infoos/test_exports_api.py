from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.exports import router
from backend.application.tasks.handlers.data_export import handle_data_export
from backend.application.tasks.repository import TaskRepository
from tests.infoos.storage.migration_test_helpers import migrate_database_path


def _client(tmp_path: Path) -> TestClient:
    migrate_database_path(tmp_path / "data" / "infoos.sqlite3")
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(router)
    return TestClient(app)


def test_export_http_submit_history_detail_and_download(tmp_path: Path) -> None:
    client = _client(tmp_path)
    request = {"kind": "data", "format": "json", "include_cards_content": True}

    estimate = client.post("/api/exports/estimate", json=request)
    submitted = client.post("/api/exports", json=request)
    task_id = submitted.json()["task_id"]
    pending = client.get(f"/api/exports/{task_id}")

    assert estimate.status_code == 200
    assert submitted.status_code == 202
    assert pending.json()["status"] == "pending"
    assert client.get("/api/exports").json()["total"] == 1
    assert client.get(f"/api/exports/{task_id}/download").status_code == 409

    repo = TaskRepository.for_base_dir(tmp_path)
    task = repo.get(task_id)
    assert task is not None
    result = handle_data_export(task, tmp_path)
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute(
            "update tasks set status='success', artifacts_summary=?, finished_at=updated_at where task_id=?",
            (__import__("json").dumps({**task.artifacts_summary, **result["artifacts"]}), task_id),
        )

    completed = client.get(f"/api/exports/{task_id}")
    download = client.get(f"/api/exports/{task_id}/download")
    assert completed.json()["artifact"]["download_url"].endswith("/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")


def test_export_http_returns_stable_validation_and_not_found_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)

    invalid = client.post("/api/exports", json={"kind": "wrong"})
    missing = client.get("/api/exports/missing")
    bad_filter = client.get("/api/exports?status=unknown")

    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_export_kind"
    assert missing.status_code == 404
    assert missing.json()["code"] == "export_not_found"
    assert bad_filter.status_code == 422
    assert bad_filter.json()["code"] == "invalid_export_filter"


def test_failed_export_download_has_specific_error(tmp_path: Path) -> None:
    client = _client(tmp_path)
    submitted = client.post(
        "/api/exports", json={"kind": "data", "include_cards_content": True}
    ).json()
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute(
            "update tasks set status='failed', error_reason='disk failed' where task_id=?",
            (submitted["task_id"],),
        )

    response = client.get(f"/api/exports/{submitted['task_id']}/download")

    assert response.status_code == 409
    assert response.json()["code"] == "export_failed"
