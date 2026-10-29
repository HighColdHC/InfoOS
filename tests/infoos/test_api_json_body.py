"""Optional request bodies must never turn malformed input into bulk work."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.routes import runs
from backend.application.workflows import source_batch


@pytest.fixture
def body_client(tmp_path):
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(runs.router)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/source-run-batches"),
])
@pytest.mark.parametrize("body", [
    b"{", b"[]", b"null", b'"text"', b"123", b"true", b"\xff",
    b'{"limit": NaN}', b'{"limit": Infinity}', b'{"limit": -Infinity}',
])
def test_bad_body_returns_400_without_downstream_work(body_client, monkeypatch, method, path, body):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("invalid body must not trigger downstream work")

    for module, name in (
        (source_batch, "enqueue_source_batch"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    response = body_client.request(method, path, content=body)
    assert response.status_code == 400
    assert response.json() == {"detail": "request body must be a JSON object"}
    assert calls == []
