"""Management auth accepts edge credentials or the existing form session."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import management_auth
from backend.infrastructure.runtime import webauth

SECRET = "management-test-only-" + "x" * 40
PATH = "/api/internal/management-authorize"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(management_auth.router)
    monkeypatch.setenv("INFOOS_EDGE_READER_SECRET", SECRET)
    monkeypatch.setattr(webauth, "gateway_session_authorized", lambda cookie, **kw: cookie == "auth=valid")
    return TestClient(app)


@pytest.mark.parametrize("secret,status", [(SECRET, 204), ("wrong", 401), ("", 401)])
@pytest.mark.parametrize("uri", ["/", "/api/sources", "/api/token-auth/tokens", "/api/exports"])
def test_management_edge_authorization(client, secret, status, uri):
    response = client.get(PATH, headers={
        "x-infoos-internal-auth": "1", "x-infoos-edge-auth": secret,
        "x-original-uri": uri, "x-infoos-edge-authenticated": "1",
    })
    assert response.status_code == status
    assert response.headers["x-infoos-edge-authenticated"] == ("1" if status == 204 else "")
    assert SECRET not in response.text
    assert SECRET not in str(response.headers)


def test_management_form_session(client, monkeypatch):
    calls = []
    def authorized(cookie, **kwargs):
        calls.append((cookie, kwargs))
        return cookie == "auth=valid"
    monkeypatch.setattr(webauth, "gateway_session_authorized", authorized)
    response = client.get(PATH, headers={
        "x-infoos-internal-auth": "1", "x-infoos-original-cookie": "auth=valid",
        "x-original-uri": "/api/sources", "x-infoos-edge-auth": "wrong",
    })
    assert response.status_code == 204
    assert response.headers["x-infoos-edge-authenticated"] == ""
    assert calls == [("auth=valid", {"original_uri": "/api/sources"})]


def test_management_endpoint_is_internal(client):
    assert client.get(PATH, headers={"x-infoos-edge-auth": SECRET}).status_code == 404
