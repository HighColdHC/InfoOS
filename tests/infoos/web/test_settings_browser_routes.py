"""Browser visibility routes: GET / PUT the runtime headless switch."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api.routes import settings_browser as routes  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("sources: []\n", encoding="utf-8")
    monkeypatch.setattr(routes, "_resolve_config_path", lambda: config_path)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), config_path


def test_get_visibility_defaults_to_true(client):
    test_client, _ = client
    response = test_client.get("/api/browser/visibility")
    assert response.status_code == 200
    assert response.json() == {"headless_during_fetch": True}


def test_put_visibility_persists_to_yaml(client):
    test_client, config_path = client
    response = test_client.put("/api/browser/visibility", json={"headless_during_fetch": False})
    assert response.status_code == 204
    text = config_path.read_text(encoding="utf-8")
    assert "headless_during_fetch: false" in text


def test_put_visibility_rejects_non_bool(client):
    test_client, _ = client
    response = test_client.put("/api/browser/visibility", json={"headless_during_fetch": "yes"})
    assert response.status_code == 422


def test_put_visibility_round_trip(client):
    test_client, _ = client
    test_client.put("/api/browser/visibility", json={"headless_during_fetch": True})
    response = test_client.get("/api/browser/visibility")
    assert response.json() == {"headless_during_fetch": True}
    test_client.put("/api/browser/visibility", json={"headless_during_fetch": False})
    response = test_client.get("/api/browser/visibility")
    assert response.json() == {"headless_during_fetch": False}
