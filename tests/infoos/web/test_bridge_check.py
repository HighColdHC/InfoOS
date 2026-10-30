"""Bridge checks must distinguish a live connection from installation facts."""
from __future__ import annotations

import subprocess
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import browser, collection_environment
from backend.infrastructure.runtime.setup_state import SetupState, load_state, save_state


@pytest.mark.parametrize(("output", "returncode", "status", "detail"), [
    ("Daemon: running (PID 1632)\nVersion: v1.8.6\nExtension: connected (v1.0.26)", 0, "connected", None),
    ("Daemon: running (PID 1632)\nExtension: disconnected", 0, "disconnected", "bridge_disconnected"),
    ("Daemon: not running", 0, "disconnected", "daemon_unavailable"),
    ("", 1, "failed", "bridge_check_failed"),
])
def test_check_bridge_persists_connection_and_get_stays_passive(
    tmp_path, monkeypatch, output, returncode, status, detail,
):
    state_path = tmp_path / "setup-state.json"
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/managed/opencli")
    monkeypatch.setattr(browser, "SETUP_STATE_PATH", state_path)
    monkeypatch.setattr(collection_environment, "SETUP_STATE_PATH", state_path)
    state = SetupState.from_dict({})
    state.component_snapshot_at = "2026-07-31T09:38:34Z"
    state.components["sqlite"].version = "3.45.1"
    save_state(state_path, state)
    run = Mock(return_value=subprocess.CompletedProcess([], returncode, output, ""))
    monkeypatch.setattr("subprocess.run", run)
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(browser.router)
    app.include_router(collection_environment.router)
    client = TestClient(app)

    before = client.get("/api/collection-environment").json()["components"][1]
    assert before["status"] == "unknown"
    assert before["version"] is None
    assert before["last_checked_at"] is None
    run.assert_not_called()

    response = client.post("/api/browser/bridge-check")
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] == (status == "connected")
    assert result["component"]["status"] == status
    assert result["component"]["detail_code"] == detail
    assert result["component"]["version"] == ("1.0.26" if status == "connected" else None)
    run.assert_called_once_with(["/managed/opencli", "daemon", "status"],
                                capture_output=True, text=True, timeout=10, check=False)
    after = client.get("/api/collection-environment").json()["components"][1]
    assert after["status"] == status
    assert after["last_checked_at"] == result["component"]["last_checked_at"]
    assert run.call_count == 1
    saved = load_state(state_path)
    assert saved.components["sqlite"].version == "3.45.1"
    assert saved.component_snapshot_at == state.component_snapshot_at


def test_check_bridge_timeout_is_saved_as_failed(tmp_path, monkeypatch):
    from backend.application.setup.environment_snapshot import check_opencli_bridge

    run = Mock(side_effect=subprocess.TimeoutExpired("opencli", 10))
    monkeypatch.setattr("subprocess.run", run)
    result = check_opencli_bridge(tmp_path / "setup-state.json")
    assert result["ok"] is False
    assert result["component"]["status"] == "failed"
    assert load_state(tmp_path / "setup-state.json").components["opencli_bridge"].last_checked
    assert run.call_count == 1
