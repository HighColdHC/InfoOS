from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.observability.logging import resolve_log_path
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository


def _write_structured_logs(tmp_path):
    path = resolve_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "timestamp": "2026-07-16T10:00:00Z", "level": "ERROR", "area": "media",
            "message": "asset failed Authorization: Bearer hidden-secret-token", "object_type": "asset",
            "object_id": "asset-1", "request_id": None, "source_id": "source-1", "task_id": "task-1", "worker_id": "worker-1",
        },
        {
            "timestamp": "2026-07-16T09:00:00Z", "level": "INFO", "area": "fetch",
            "message": "source fetched", "object_type": "source", "object_id": "source-1",
            "request_id": "req-file", "source_id": "source-1", "task_id": "task-2", "worker_id": "worker-2",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _append_interface_request(tmp_path):
    ApiRequestLogRepository(tmp_path).append(
        request_id="req-api-1",
        token_id=None,
        token_prefix=None,
        client_label="Obsidian",
        method="GET",
        path="/api/plugin/v1/cards",
        status_code=206,
        duration_ms=12,
        response_bytes=42,
        result_code="partial_content",
        result_summary="returned cards Cookie: sid=private",
        created_at="2026-07-16T11:00:00Z",
    )


def test_logs_route_unions_file_and_interface_audit_with_server_filters(tmp_path):
    app = create_app(base_dir=tmp_path)
    _write_structured_logs(tmp_path)
    _append_interface_request(tmp_path)
    client = TestClient(app)

    response = client.get("/api/logs", params={"area": "interface", "request_id": "req-api-1", "limit": 25, "offset": 0})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["next_offset"] is None
    assert payload["summary"] == {"total": 1, "info": 1, "warning": 0, "error": 0}
    item = payload["items"][0]
    assert item["area"] == "interface"
    assert item["request_id"] == "req-api-1"
    assert item["object_type"] == "request"
    assert "private" not in item["message"]
    assert set(item) == {
        "log_id", "timestamp", "level", "area", "message", "object_type", "object_id",
        "request_id", "source_id", "task_id", "worker_id",
    }


def test_logs_route_paginates_stably_and_detail_matches_redacted_list(tmp_path):
    app = create_app(base_dir=tmp_path)
    _write_structured_logs(tmp_path)
    client = TestClient(app)

    page = client.get(
        "/api/logs",
        params={"query": "source", "time_from": "2026-07-16T08:30:00Z", "time_to": "2026-07-16T10:30:00Z", "limit": 1, "offset": 0},
    )

    assert page.status_code == 200
    payload = page.json()
    assert payload["total"] == 2
    assert payload["next_offset"] == 1
    item = payload["items"][0]
    detail = client.get(f"/api/logs/{item['log_id']}")
    assert detail.status_code == 200
    assert detail.json() == item
    assert "hidden-secret-token" not in json.dumps(detail.json())


def test_logs_route_filters_level_object_and_returns_stable_errors(tmp_path):
    app = create_app(base_dir=tmp_path)
    _write_structured_logs(tmp_path)
    client = TestClient(app)

    response = client.get("/api/logs", params={"level": "ERROR", "object_id": "asset-1"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["area"] == "media"

    missing = client.get("/api/logs/not-a-real-log")
    assert missing.status_code == 404
    assert missing.json()["code"] == "log_not_found"

    invalid = client.get("/api/logs", params={"area": "decision"})
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_log_area"


def test_logs_route_uses_real_timestamp_boundaries(tmp_path):
    app = create_app(base_dir=tmp_path)
    _write_structured_logs(tmp_path)
    path = resolve_log_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("2026-07-16 08:00:00,000 INFO infoos.system Authorization: Bearer legacy-secret-token-value\n")
    client = TestClient(app)

    exact = client.get(
        "/api/logs",
        params={"time_from": "2026-07-16T10:00:00Z", "time_to": "2026-07-16T10:00:00Z"},
    )
    assert exact.status_code == 200
    assert exact.json()["total"] == 1
    assert exact.json()["items"][0]["object_id"] == "asset-1"
