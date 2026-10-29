"""Reader auth accepts only read-only surface requests and never logs secrets."""
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import reader_auth
from backend.infrastructure.runtime import webauth

SECRET = "test-reader-secret-" + "x" * 40


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(reader_auth.router)
    monkeypatch.setenv("INFOOS_EDGE_READER_SECRET", SECRET)
    monkeypatch.setattr(webauth, "gateway_session_authorized", lambda *a, **kw: False)
    return TestClient(app)


def headers(**changes):
    return {"X-InfoOS-Internal-Auth": "1", "X-Original-URI": "/r/card/one",
            "X-Original-Method": "GET", "X-InfoOS-Edge-Auth": SECRET, **changes}


@pytest.mark.parametrize("supplied,configured,status", [
    (SECRET, SECRET, 204), ("wrong", SECRET, 401), ("", SECRET, 401),
    (SECRET, None, 401), ("", "", 401), ("short", "short", 401),
])
def test_secret(client, monkeypatch, caplog, supplied, configured, status):
    if configured is None:
        monkeypatch.delenv("INFOOS_EDGE_READER_SECRET")
    else:
        monkeypatch.setenv("INFOOS_EDGE_READER_SECRET", configured)
    with caplog.at_level(logging.DEBUG):
        response = client.get("/api/internal/reader-authorize", headers=headers(**{"X-InfoOS-Edge-Auth": supplied}))
    assert response.status_code == status
    assert response.content == b""
    assert SECRET not in caplog.text
    assert "test-reader-secret" not in caplog.text


@pytest.mark.parametrize("uri", ["/api/token-auth/tokens", "/browser-console/",
    "/api/card-preview/captures/one", "/api/intelligence/daily-brief/2026-09-30/rebuild",
    "/r/../api/config", "/r/%2e%2e/api/config", "//evil/r/card/one", "", "/assets/../api/config"])
def test_wrong_path(client, uri):
    assert client.get("/api/internal/reader-authorize", headers=headers(**{"X-Original-URI": uri})).status_code == 401


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "OPTIONS", "", "get"])
def test_wrong_method(client, method):
    assert client.get("/api/internal/reader-authorize", headers=headers(**{"X-Original-Method": method})).status_code == 401


@pytest.mark.parametrize("uri", ["/favicon.ico", "/r/card/one", "/r/brief/2026-09-30", "/assets/index.js", "/api/card-preview/one?x=1",
    "/api/card-preview/assets/one", "/api/intelligence/daily-brief/2026-09-30", "/api/intelligence/knowledge", "/api/intelligence/knowledge/kc_one", "/r/knowledge", "/r/knowledge/kc_one"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_reader_paths(client, uri, method):
    assert client.get("/api/internal/reader-authorize", headers=headers(**{"X-Original-URI": uri, "X-Original-Method": method})).status_code == 204


def test_requires_internal_marker(client):
    assert client.get("/api/internal/reader-authorize", headers=headers(**{"X-InfoOS-Internal-Auth": ""})).status_code == 401


def test_form_session(client, monkeypatch):
    calls = []
    def authorized(cookie, **kw):
        calls.append((cookie, kw))
        return cookie == "session=valid"
    monkeypatch.setattr(webauth, "gateway_session_authorized", authorized)
    assert client.get("/api/internal/reader-authorize", headers=headers(**{
        "X-InfoOS-Edge-Auth": "", "X-InfoOS-Original-Cookie": "session=valid"})).status_code == 204
    assert calls == [("session=valid", {"original_uri": "/r/card/one"})]
    assert client.get("/api/internal/reader-authorize", headers=headers(**{
        "X-Original-Method": "POST", "X-InfoOS-Original-Cookie": "session=valid"})).status_code == 401


def test_shared_webauth_socket(monkeypatch):
    import httpx
    seen = []
    monkeypatch.setenv("INFOOS_WEBAUTH_SOCKET", "/private/tmp/reader-webauth.sock")
    def transport(**kw):
        assert kw == {"uds": "/private/tmp/reader-webauth.sock"}
        def handle(request):
            seen.append(request)
            return httpx.Response(204)
        return httpx.MockTransport(handle)
    monkeypatch.setattr(webauth.httpx, "HTTPTransport", transport)
    assert webauth.gateway_session_authorized("session=valid", original_uri="/r/card/one")
    assert seen[0].headers["cookie"] == "session=valid"
    assert seen[0].headers["x-original-uri"] == "/r/card/one"
    assert not webauth.gateway_session_authorized("")


@pytest.mark.parametrize("path", ["/favicon.ico", "/r/card/one", "/r/brief/2026-09-30",
    "/api/card-preview/one", "/api/card-preview/assets/one", "/api/intelligence/daily-brief/2026-09-30", "/api/intelligence/knowledge/kc_one", "/r/knowledge", "/r/knowledge/kc_one"])
def test_product_reader_head_routes(tmp_path, path):
    from backend.app.main import create_app
    app = create_app(tmp_path)
    response = TestClient(app).head(path)
    assert response.status_code in {204, 404}
    assert response.content == b""


def test_browse_list_uses_existing_reader_auth(client, monkeypatch):
    uri = "/api/card-preview?evaluation=passed&limit=20"
    for method in ("GET", "HEAD"):
        assert reader_auth.is_reader_request("/api/card-preview", method)
        request_headers = headers(**{"X-Original-URI": uri, "X-Original-Method": method})
        assert client.get("/api/internal/reader-authorize", headers=request_headers).status_code == 204
        request_headers["X-InfoOS-Edge-Auth"] = ""
        assert client.get("/api/internal/reader-authorize", headers=request_headers).status_code == 401
    monkeypatch.setattr(webauth, "gateway_session_authorized", lambda cookie, **kw: cookie == "session=valid")
    request_headers["X-InfoOS-Original-Cookie"] = "session=valid"
    assert client.get("/api/internal/reader-authorize", headers=request_headers).status_code == 204
    request_headers["X-Original-Method"] = "POST"
    assert client.get("/api/internal/reader-authorize", headers=request_headers).status_code == 401


@pytest.mark.parametrize("path,method,status", [
    ("/api/opportunities/board", "GET", 204), ("/api/opportunities/stats?window=7d", "GET", 204),
    ("/api/opportunities/card-1", "HEAD", 204), ("/r/opportunities", "GET", 204),
    ("/r/opportunities/stats", "HEAD", 204), ("/api/opportunities/card-1/follow-up", "PUT", 204),
    ("/api/opportunities/card-1/follow-up", "DELETE", 401), ("/api/opportunities/board", "PUT", 401),
    ("/api/opportunities/card-1", "PUT", 401), ("/api/card-preview/card-1", "PUT", 401),
    ("/r/opportunities", "PUT", 401), ("/api/opportunities/card-1/follow-up/extra", "PUT", 401),
])
def test_opportunity_reader_paths(client, path, method, status):
    assert client.get("/api/internal/reader-authorize", headers=headers(**{
        "X-Original-URI": path, "X-Original-Method": method})).status_code == status
