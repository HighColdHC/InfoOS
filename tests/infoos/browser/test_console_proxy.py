from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import browser_console
from backend.api.routes import browser, management_auth


class _FakeWebSocket:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.closed = None

    async def close(self, code=1000, reason=None):
        self.closed = (code, reason)


def test_proxy_rejects_missing_outer_gateway_session():
    websocket = _FakeWebSocket()
    with patch("backend.infrastructure.runtime.webauth.gateway_session_authorized", return_value=False):
        asyncio.run(browser_console.proxy_browser_console(websocket))
    assert websocket.closed == (1008, None)


def test_authorization_guard_closes_existing_socket_at_server_deadline():
    websocket = _FakeWebSocket()
    with patch.object(browser_console, "grant_remaining_seconds", return_value=0):
        asyncio.run(browser_console._authorization_guard(websocket, "grant", "session"))
    assert websocket.closed == (1008, "Browser Console authorization expired")


def test_proxy_accepts_verified_edge_auth_without_form_session(monkeypatch):
    secret = "websocket-test-only-" + "x" * 40
    monkeypatch.setenv("INFOOS_EDGE_READER_SECRET", secret)
    monkeypatch.setattr("backend.infrastructure.runtime.webauth.gateway_session_authorized", lambda *a, **kw: False)
    reader = Mock(read=AsyncMock(return_value=b""))
    writer = Mock(wait_closed=AsyncMock())
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(browser_console.asyncio, "open_unix_connection", connect)
    app = FastAPI()
    app.include_router(management_auth.router)
    app.include_router(browser.router)
    with TestClient(app) as client:
        auth = client.get("/api/internal/management-authorize", headers={
            "x-infoos-internal-auth": "1", "x-infoos-edge-auth": secret,
        })
        assert auth.status_code == 204
        with client.websocket_connect("/api/browser/console-websocket", headers={
            "x-infoos-edge-authenticated": auth.headers["x-infoos-edge-authenticated"],
            "x-infoos-console-grant": browser.issue_grant(),
            "x-infoos-original-origin": "https://infoos.example",
            "x-forwarded-host": "infoos.example", "x-forwarded-proto": "https",
        }):
            pass
    connect.assert_awaited_once()
