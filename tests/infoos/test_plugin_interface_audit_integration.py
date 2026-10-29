from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_real_app_authenticates_audits_and_exposes_request_history(tmp_path) -> None:
    client = TestClient(create_app(tmp_path))
    created = client.post(
        "/api/token-auth/tokens",
        json={"name": "Obsidian integration", "scopes": ["cards:read"]},
    )
    assert created.status_code == 201
    plaintext = created.json()["plaintext_token"]

    accepted = client.get(
        "/api/plugin/v1/capabilities",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    rejected = client.get("/api/plugin/v1/capabilities")

    assert accepted.status_code == 200
    assert accepted.headers["x-request-id"].startswith("req_")
    assert rejected.status_code == 401
    assert rejected.json()["request_id"] == rejected.headers["x-request-id"]

    history = client.get(
        "/api/token-auth/request-logs",
        params={"time_range": "24h", "limit": 25, "offset": 0},
    )
    assert history.status_code == 200
    items = {item["request_id"]: item for item in history.json()["items"]}
    assert items[accepted.headers["x-request-id"]]["result_code"] == "ok"
    assert items[rejected.headers["x-request-id"]]["result_code"] == "missing_token"
    assert history.json()["summary"]["request_count"] == 2
    assert history.json()["summary"]["success_2xx_count"] == 1
    assert history.json()["summary"]["client_error_4xx_count"] == 1

    token_rows = client.get("/api/token-auth/tokens").json()["items"]
    assert token_rows[0]["last_used_at"] is not None
    assert plaintext not in str(history.json())
