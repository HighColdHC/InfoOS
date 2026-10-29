"""Tests for /api/browser/* routes: session lifecycle and login actions."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api.routes import browser as browser_routes  # noqa: E402
from backend.infrastructure.browser import session_state  # noqa: E402


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Isolated TestClient with a fresh session_state JSON path."""
    json_path = None  # assigned in tests
    app = FastAPI()
    app.include_router(browser_routes.router)
    monkeypatch.setattr(browser_routes, "SETUP_STATE_PATH", tmp_path / "setup-state.json")
    return TestClient(app)


def _write_ready_session(json_path: Path) -> None:
    json_path.write_text(json.dumps({
        "schema_version": 1, "context_id": "abc", "alias": "infoos-fetch",
        "profile_dir": "/x", "started_at": "t", "last_health_at": "t",
        "last_health_status": "ready",
    }))


def _write_ready_session_for_profile(json_path: Path, profile_dir: Path) -> None:
    json_path.write_text(json.dumps({
        "schema_version": 1, "context_id": "abc", "alias": "infoos-fetch",
        "profile_dir": str(profile_dir), "started_at": "t", "last_health_at": "t",
        "last_health_status": "ready",
    }))


def _write_cookie_db(profile_dir: Path, rows: list[tuple[str, str]]) -> None:
    import sqlite3

    db_path = profile_dir / "Default" / "Network" / "Cookies"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT)")
        conn.executemany("INSERT INTO cookies (host_key, name) VALUES (?, ?)", rows)
        conn.commit()
    finally:
        conn.close()


def _write_setup_state(path: Path, accounts: dict[str, dict]) -> None:
    path.write_text(json.dumps({
        "version": 1,
        "current_step": "login_accounts",
        "completed_steps": [],
        "accounts": accounts,
    }))


def test_get_browser_session_when_no_json(client, tmp_path, monkeypatch):
    """GET /api/browser/session returns status='not_started' when JSON is missing."""
    json_path = tmp_path / "missing.json"
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    session_state._cache_clear()
    resp = client.get("/api/browser/session")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_started"


def test_get_browser_session_when_ready(client, tmp_path, monkeypatch):
    """GET /api/browser/session returns full state when JSON is well-formed."""
    json_path = tmp_path / "browser-session.json"
    _write_ready_session(json_path)
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    session_state._cache_clear()
    resp = client.get("/api/browser/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["context_id"] == "abc"
    assert data["alias"] == "infoos-fetch"


def test_post_browser_session_launch_starts_process(client, monkeypatch):
    """POST /api/browser/session {action: launch} starts/reuses the launcher."""
    calls = []
    monkeypatch.setattr(
        "backend.application.setup.browser_session.launch_infoos_chrome",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "start_url": "https://x.com/home"},
    )
    resp = client.post("/api/browser/session", json={"action": "launch"})
    assert resp.status_code == 202
    assert resp.json()["ok"] is True
    assert "task_id" in resp.json()
    assert resp.json()["console_url"] == "/browser-console/"
    assert "infoos_browser_console=" in resp.headers["set-cookie"]
    assert "HttpOnly" in resp.headers["set-cookie"]
    assert "SameSite=strict" in resp.headers["set-cookie"]
    assert "Secure" in resp.headers["set-cookie"]
    assert calls == [{}]


def test_browser_console_authorization_is_internal_and_same_origin(client):
    with patch("backend.infrastructure.runtime.webauth.gateway_session_authorized", return_value=True):
        _assert_browser_console_authorization(client)


def _assert_browser_console_authorization(client):
    token = browser_routes.issue_grant()
    path = "/api/browser/console-authorize"
    assert client.get(path).status_code == 404

    headers = {
        "x-infoos-internal-auth": "1",
        "x-infoos-console-grant": token,
        "x-infoos-original-cookie": "infoos_session=valid",
        "x-infoos-original-referer": "https://infoos.example/browser-console/",
        "x-forwarded-host": "infoos.example",
        "x-forwarded-proto": "https",
    }
    assert client.get(path, headers=headers).status_code == 204
    headers["x-infoos-original-referer"] = "https://attacker.example/"
    assert client.get(path, headers=headers).status_code == 401


def test_browser_console_authorization_accepts_edge_secret(client, monkeypatch):
    secret = "browser-console-test-only-" + "x" * 40
    monkeypatch.setenv("INFOOS_EDGE_READER_SECRET", secret)
    with patch("backend.infrastructure.runtime.webauth.gateway_session_authorized", return_value=False):
        headers = {
            "x-infoos-internal-auth": "1",
            "x-infoos-edge-auth": secret,
            "x-infoos-console-grant": browser_routes.issue_grant(),
            "x-infoos-original-referer": "https://infoos.example/browser-console/",
            "x-forwarded-host": "infoos.example", "x-forwarded-proto": "https",
        }
        path = "/api/browser/console-authorize"
        assert client.get(path, headers=headers).status_code == 204
        headers["x-infoos-edge-auth"] = "wrong"
        assert client.get(path, headers=headers).status_code == 401
        headers["x-infoos-edge-auth"] = secret
        headers["x-infoos-console-grant"] = ""
        assert client.get(path, headers=headers).status_code == 401
        headers["x-infoos-console-grant"] = browser_routes.issue_grant()
        headers["x-infoos-original-referer"] = "https://attacker.example/"
        assert client.get(path, headers=headers).status_code == 401


def test_post_browser_session_unknown_action_returns_stable_422(client):
    """POST with unknown action is rejected by the typed request contract."""
    resp = client.post("/api/browser/session", json={"action": "bogus"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "unsupported_browser_action"


def test_post_browser_login_check_requires_platform(client):
    """POST /api/browser/login-check with no platform returns 422."""
    resp = client.post("/api/browser/login-check", json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == "platform_required"


def test_post_browser_login_check_with_session_is_passive(client, tmp_path, monkeypatch):
    """POST /api/browser/login-check refreshes from cookies, not OpenCLI."""
    json_path = tmp_path / "browser-session.json"
    profile_dir = tmp_path / "chrome-profile"
    _write_cookie_db(profile_dir, [(".reddit.com", "reddit_session")])
    _write_ready_session_for_profile(json_path, profile_dir)
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    session_state._cache_clear()

    resp = client.post("/api/browser/login-check", json={"platform": "reddit"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["status"] == "logged_in"
    from backend.infrastructure.runtime.setup_state import AccountStatus, load_state

    assert load_state(browser_routes.SETUP_STATE_PATH).accounts["reddit"].status == AccountStatus.VERIFIED


def test_post_browser_login_check_without_binding_reads_dedicated_profile_cookies(
    client,
    tmp_path,
    monkeypatch,
):
    json_path = tmp_path / "missing-browser-session.json"
    profile_dir = tmp_path / "chrome-profile"
    _write_cookie_db(profile_dir, [(".x.com", "auth_token"), (".x.com", "ct0")])
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    monkeypatch.setattr(
        "backend.application.setup.browser_session.chrome_profile_dir",
        lambda: profile_dir,
    )
    session_state._cache_clear()

    resp = client.post("/api/browser/login-check", json={"platform": "x"})

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["status"] == "logged_in"
    assert resp.json()["method"] == "cookie"


def test_post_browser_login_launch_opens_platform_url(client, monkeypatch):
    calls = []

    def fake_launch_infoos_chrome(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "launcher": "/launcher", "start_url": kwargs["start_url"]}

    monkeypatch.setattr(
        "backend.application.setup.browser_session.launch_infoos_chrome",
        fake_launch_infoos_chrome,
    )

    resp = client.post("/api/browser/login-launch", json={"platform": "reddit"})

    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "platform": "reddit",
        "start_url": "https://www.reddit.com/",
        "console_url": "/browser-console/",
    }
    assert calls == [{"start_url": "https://www.reddit.com/"}]


def test_post_browser_login_check_updates_only_target_account(client, tmp_path, monkeypatch):
    from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus, SetupState, load_state, save_state

    state = SetupState.from_dict({})
    state.accounts["x"] = AccountState(status=AccountStatus.VERIFIED, last_checked="old")
    save_state(browser_routes.SETUP_STATE_PATH, state)
    json_path = tmp_path / "missing-browser-session.json"
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    monkeypatch.setattr(
        "backend.application.setup.browser_session.chrome_profile_dir",
        lambda: tmp_path / "missing-profile",
    )
    session_state._cache_clear()

    response = client.post("/api/browser/login-check", json={"platform": "youtube"})

    assert response.status_code == 200
    loaded = load_state(browser_routes.SETUP_STATE_PATH)
    assert loaded.accounts["youtube"].status == AccountStatus.NEEDS_LOGIN
    assert loaded.accounts["youtube"].last_checked is not None
    assert loaded.accounts["x"].status == AccountStatus.VERIFIED
    assert loaded.accounts["x"].last_checked == "old"


def test_passive_logged_out_invalidates_but_logged_in_does_not_grant_persistence(
    client,
    tmp_path,
    monkeypatch,
):
    from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus, SetupState, load_state, save_state

    state = SetupState.from_dict({})
    state.accounts["x"] = AccountState(
        status=AccountStatus.VERIFIED,
        persisted=True,
        persistence_checked_at="old-check",
        persistence_stage="verified",
    )
    state.accounts["reddit"] = AccountState(status=AccountStatus.UNKNOWN)
    save_state(browser_routes.SETUP_STATE_PATH, state)
    profile_dir = tmp_path / "chrome-profile"
    _write_cookie_db(profile_dir, [(".reddit.com", "reddit_session")])
    monkeypatch.setattr(session_state, "JSON_PATH", tmp_path / "missing-session.json")
    monkeypatch.setattr(
        "backend.application.setup.browser_session.chrome_profile_dir",
        lambda: profile_dir,
    )
    session_state._cache_clear()

    assert client.post("/api/browser/login-check", json={"platform": "x"}).json()["status"] == "logged_out"
    assert client.post("/api/browser/login-check", json={"platform": "reddit"}).json()["status"] == "logged_in"

    loaded = load_state(browser_routes.SETUP_STATE_PATH)
    assert loaded.accounts["x"].persisted is False
    assert loaded.accounts["x"].persistence_stage == "passive_logged_out"
    assert loaded.accounts["reddit"].status == AccountStatus.VERIFIED
    assert loaded.accounts["reddit"].persisted is False


@pytest.mark.parametrize("path", ["login-check", "login-launch"])
def test_browser_platform_action_rejects_non_whitelisted_platform(client, path):
    response = client.post(f"/api/browser/{path}", json={"platform": "file:///tmp/evil"})

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_login_platform"
