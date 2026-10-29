from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
import subprocess
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.api.routes import lan_account as routes
from backend.api.routes import management_auth
from backend.api.security import SameOriginManagementMiddleware
from backend.application.usecases import lan_account as usecase
from backend.infrastructure.runtime import webauth, webauth_control
from backend.infrastructure.security import lan_passwords as passwords

OLD = "initial-test-only-password"
NEW = "replacement-test-only-secret"


@pytest.fixture(autouse=True)
def _fast_bcrypt_except_cost_check(request, monkeypatch):
    # Keep the production cost in the test that asserts it; other tests still
    # exercise real bcrypt verification and persistence at a lower test cost.
    if request.node.name == "test_bcrypt_apr1_and_atomic_permissions":
        return
    import bcrypt

    gensalt = bcrypt.gensalt
    monkeypatch.setattr(bcrypt, "gensalt", lambda **kwargs: gensalt(rounds=4))


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "htpasswd"
    monkeypatch.setenv("INFOOS_WEBAUTH_PASSWORD_FILE", str(path))
    value = passwords.PasswordStore(path)
    value.write({"tester": passwords.hash_password(OLD)})
    return value


@pytest.fixture
def client(store, monkeypatch):
    app = FastAPI()
    app.add_middleware(SameOriginManagementMiddleware)
    app.include_router(routes.router)
    app.include_router(management_auth.router)
    monkeypatch.setattr(webauth, "gateway_session_authorized", lambda cookie, **kw: cookie == "auth=test-session")
    monkeypatch.setattr(usecase, "reload_and_invalidate_sessions", Mock())
    monkeypatch.setattr(usecase, "get_logger", lambda name: logging.getLogger("test.lan.audit"))
    client = TestClient(app, headers={"cookie": "auth=test-session", "x-real-ip": "192.168.1.2"})
    return client


def change(client, **overrides):
    data = dict(current_password=OLD, new_password=NEW, confirm_password=NEW)
    data.update(overrides)
    return client.post("/api/lan-account/password", json=data)


def test_bcrypt_apr1_and_atomic_permissions(store, monkeypatch):
    before = store.path.stat()
    encoded = store.read()["tester"]
    assert int(encoded.split("$")[2]) >= 10
    assert passwords.verify_password(OLD, encoded)
    assert not passwords.verify_password("wrong", encoded)
    legacy = subprocess.run(["openssl", "passwd", "-apr1", "-salt", "test1234", "-stdin"],
                            input=OLD + "\n", text=True, capture_output=True, check=True).stdout.strip()
    assert passwords.verify_password(OLD, legacy)
    assert not passwords.verify_password("wrong", legacy)
    assert not passwords.verify_password(OLD + "\nignored", legacy)
    replace = passwords.os.replace
    calls = []
    def check_replace(source, target):
        assert os.stat(source).st_mode & 0o777 == 0o600
        assert str(source).startswith(str(store.path.parent))
        calls.append((source, target))
        replace(source, target)
    monkeypatch.setattr(passwords.os, "replace", check_replace)
    store.write({"tester": encoded})
    after = store.path.stat()
    assert before.st_ino != after.st_ino
    assert (before.st_uid, before.st_gid) == (after.st_uid, after.st_gid)
    assert after.st_mode & 0o777 == 0o600
    assert calls and not list(store.path.parent.glob(".htpasswd.*"))


def test_concurrent_changes_hold_lock_across_replace(store):
    def add(index):
        with passwords.PasswordStore(store.path).locked() as locked:
            users = locked.read()
            users[f"user{index}"] = users["tester"]
            locked.write(users)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add, range(8)))
    assert len(store.read()) == 9


def test_failure_before_rename_preserves_database(store, monkeypatch):
    before = store.path.read_bytes()
    monkeypatch.setattr(passwords.os, "replace", Mock(side_effect=OSError("test failure")))
    with pytest.raises(OSError):
        store.write({"other": "placeholder"})
    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob(".htpasswd.*"))


def test_session_and_same_origin(client):
    assert client.get("/api/lan-account", headers={"cookie": ""}).status_code == 401
    assert client.post("/api/lan-account/password", json={}, headers={"cookie": ""}).status_code == 401
    response = client.post("/api/lan-account/password", json={}, headers={"origin": "https://other.example"})
    assert response.status_code == 403
    assert "同一站点" in response.json()["detail"]
    assert client.get("/api/lan-account").json() == {
        "username": "tester", "legacy_hash": False, "requires_current_username": False,
    }


def test_edge_auth_preserves_current_credentials_requirement(client, store, monkeypatch):
    secret = "account-test-only-" + "x" * 40
    monkeypatch.setenv("INFOOS_EDGE_READER_SECRET", secret)
    client.headers["cookie"] = ""
    auth = client.get("/api/internal/management-authorize", headers={
        "x-infoos-internal-auth": "1", "x-infoos-edge-auth": secret,
    })
    assert auth.status_code == 204
    client.headers["x-infoos-edge-authenticated"] = auth.headers["x-infoos-edge-authenticated"]
    assert client.get("/api/lan-account").json()["username"] == "tester"
    assert change(client, current_password="wrong").status_code == 400
    assert passwords.verify_password(OLD, store.read()["tester"])
    # Multiple local accounts still require identifying the current username.
    store.write({"tester": store.read()["tester"], "other": store.read()["tester"]})
    assert change(client).status_code == 400
    assert change(client, current_username="tester").status_code == 200
    assert passwords.verify_password(NEW, store.read()["tester"])
    assert passwords.verify_password(OLD, store.read()["other"])


def test_wrong_password_and_persistent_rate_limit(client, store):
    for _ in range(5):
        assert change(client, current_password="wrong").status_code == 400
    assert change(client).status_code == 429
    # Limits are not tied to a route instance or process-local dictionary.
    assert store.path.with_name("htpasswd.failures").exists()
    assert passwords.verify_password(OLD, store.read()["tester"])


@pytest.mark.parametrize("value", ["short", OLD, "tester-containing-password", "测" * 25, "new\nline-secret-long"])
def test_password_policy(client, value):
    assert change(client, new_password=value, confirm_password=value).status_code == 400


def test_confirmation_and_safe_validation(client):
    assert change(client, confirm_password="different").status_code == 400
    response = change(client, current_password={"secret": OLD})
    assert response.status_code == 400
    assert OLD not in response.text


def test_password_success_invalidates_and_audits(client, store, caplog):
    with caplog.at_level(logging.INFO, logger="test.lan.audit"):
        result = change(client)
    assert result.status_code == 200
    assert result.json()["reauthenticate"]
    assert passwords.verify_password(NEW, store.read()["tester"])
    assert not passwords.verify_password(OLD, store.read()["tester"])
    usecase.reload_and_invalidate_sessions.assert_called_once()
    assert all(value not in caplog.text for value in (OLD, NEW, store.read()["tester"]))
    assert all(value in caplog.text for value in ("tester", "192.168.1.2", "success", "password"))
    assert "auth=" in result.headers["set-cookie"] and "Max-Age=0" in result.headers["set-cookie"]


@pytest.mark.parametrize("name", ["a", "a b", "bad:name", "用户abc", "a" * 129, "tester"])
def test_invalid_or_conflicting_name(client, name):
    assert client.post("/api/lan-account/username", json={"current_password": OLD, "new_username": name}).status_code == 400


def test_multiuser_rename_preserves_legacy_hash_and_no_enumeration(client, store, caplog):
    legacy = subprocess.run(["openssl", "passwd", "-apr1", "-salt", "test1234", "-stdin"],
                            input=OLD + "\n", text=True, capture_output=True, check=True).stdout.strip()
    store.write({"tester": legacy})
    assert client.get("/api/lan-account").json()["legacy_hash"] is True
    store.write({"tester": legacy, "other": legacy})
    assert client.get("/api/lan-account").json()["requires_current_username"]
    bad = {"current_password": OLD, "new_username": "new@example.com"}
    assert client.post("/api/lan-account/username", json=bad).status_code == 400
    with caplog.at_level(logging.INFO, logger="test.lan.audit"):
        response = client.post("/api/lan-account/username", json=dict(bad, current_username="tester"))
    assert response.status_code == 200
    assert store.read() == {"other": legacy, "new@example.com": legacy}
    assert "tester" in caplog.text and "new@example.com" in caplog.text and legacy not in caplog.text


def test_restart_failure_is_not_reported_as_success(client, store, monkeypatch):
    monkeypatch.setattr(usecase, "reload_and_invalidate_sessions", Mock(side_effect=RuntimeError("secret diagnostic")))
    response = change(client)
    assert response.status_code == 503
    assert "凭据已写入" in response.text and "secret diagnostic" not in response.text
    assert passwords.verify_password(NEW, store.read()["tester"])


def test_control_sighup_then_restart(monkeypatch):
    monkeypatch.setattr(webauth_control, "webauth_pids", Mock(side_effect=[{91}, {91}, {92}]))
    monkeypatch.setattr(webauth_control, "webauth_ready", lambda: True)
    kill = Mock()
    monkeypatch.setattr(webauth_control.os, "kill", kill)
    monkeypatch.setattr(webauth_control.time, "sleep", lambda _: None)
    webauth_control.reload_and_invalidate_sessions()
    assert [call.args for call in kill.call_args_list] == [(91, signal.SIGHUP), (91, signal.SIGTERM)]
