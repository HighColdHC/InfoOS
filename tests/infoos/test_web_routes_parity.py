"""Basic responses for the app's document and health endpoints."""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_favicon_returns_no_content():
    client = TestClient(create_app())
    response = client.get("/favicon.ico")
    assert response.status_code == 204


def test_healthz_endpoint_responds():
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.get("/healthz?deep=1").json() == {"status": "ok"}


def test_index_page_reports_missing_frontend_dist(tmp_path):
    # FastAPI no longer renders the dashboard itself. Without a React
    # build artifact, document routes report a clear missing-dist state.
    app = create_app(base_dir=tmp_path)
    response = TestClient(app).get("/")
    assert response.status_code == 404
    assert response.json()["error"] == "frontend_dist_missing"
