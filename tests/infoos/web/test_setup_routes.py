"""Setup routes: status / launch / verify-extension / verify-platform / first-fetch."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.api.routes import setup as setup_routes  # noqa: E402
from backend.application.setup import actions  # noqa: E402
from backend.application.tasks.repository import TaskRepository  # noqa: E402
from backend.application.tasks.types import TaskStatus, TaskType  # noqa: E402
from backend.application.tasks.worker import run_tasks_by_ids  # noqa: E402
from backend.infrastructure.browser import session_state  # noqa: E402
from backend.infrastructure.storage.repositories import SourceRepository  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    state_path = tmp_path / "setup-state.json"
    flag_path = tmp_path / "setup-complete.flag"
    browser_session_path = tmp_path / "browser-session.json"
    monkeypatch.setattr(setup_routes, "SETUP_STATE_PATH", state_path)
    monkeypatch.setattr(setup_routes, "SETUP_COMPLETE_FLAG", flag_path)
    monkeypatch.setattr(session_state, "JSON_PATH", browser_session_path)
    monkeypatch.setattr(actions, "chrome_profile_dir", lambda: tmp_path / "chrome-profile")
    monkeypatch.setattr(
        "backend.application.setup.environment_snapshot.refresh_component_snapshot",
        lambda _state_path: {},
    )
    session_state._cache_clear()
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(setup_routes.router)
    yield TestClient(app)
    session_state._cache_clear()


def _install_proven_binder(monkeypatch, *, context_id: str = "ctx-dedicated") -> list[bool]:
    calls: list[bool] = []

    def bind():
        calls.append(True)
        session_state.write(session_state.BrowserSessionState(
            schema_version=2,
            profile_dir=str(actions.chrome_profile_dir()),
            context_id=context_id,
            alias="infoos-fetch",
            started_at="2026-07-22T00:00:00Z",
            last_health_at="2026-07-22T00:00:00Z",
            last_health_status="ready",
            chrome_pid=4321,
            binding_proof="new_context_after_dedicated_launch",
        ))
        return {"ok": True}

    monkeypatch.setattr(actions, "bind_infoos_chrome", bind)
    return calls


def test_get_status_returns_default_state_when_missing(client):
    response = client.get("/api/setup/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["current_step"] == "fresh"
    assert payload["wizard_opens"] == 0


def test_post_launch_invokes_launcher_and_does_not_advance(client, monkeypatch):
    """Launching the dedicated Chrome is fire-and-forget; it must not
    advance the wizard's current_step or write to completed_steps.
    It is no longer a flow step — the user can launch Chrome any time."""
    monkeypatch.setattr(
        setup_routes, "launch_infoos_chrome",
        lambda **kwargs: {"ok": True, "launcher": "/x", "start_url": "https://x.com/home"},
    )
    response = client.post("/api/setup/launch")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["console_url"] == "/browser-console/"
    assert "infoos_browser_console=" in response.headers["set-cookie"]
    # current_step / completed_steps must NOT change from the launch
    # endpoint alone. The wizard UI owns flow state, not this endpoint.
    state = client.get("/api/setup/status").json()
    assert "launch_chrome" not in state["completed_steps"]
    assert state["current_step"] in ("fresh", "install_extension")


def test_post_verify_extension_bound_when_a_context_is_connected(client, monkeypatch):
    monkeypatch.setattr(
        actions, "inspect_opencli_extension",
        lambda: {"loaded": True, "id": "infoos", "manifest_version": 3},
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: {
            "daemon_running": True,
            "daemon_version": "1.8.0",
            "extension_connected": True,
            "doctor_status": "ok",
            "profiles": [{"alias": "ctx-abc", "status": "connected", "version": "1.0.20"}],
            "last_checked_at": "2026-06-21T00:00:00Z",
            "hint": None,
        },
    )
    _install_proven_binder(monkeypatch, context_id="ctx-abc")

    response = client.post("/api/setup/verify-extension")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ok"] is True
    assert payload["status"] == "bound"
    assert payload["context_id"] == "ctx-abc"
    assert payload["connected_profiles"] == ["ctx-abc"]
    assert payload["current_step"] == "login_accounts"

    binding = session_state.read()
    assert binding is not None
    assert binding.context_id == "ctx-abc"
    assert binding.alias == "infoos-fetch"
    assert session_state.is_alive(binding) is True


def test_successful_extension_binding_refreshes_component_snapshot(client, monkeypatch):
    calls = []
    monkeypatch.setattr(actions, "inspect_opencli_extension", lambda: {"loaded": True, "id": "infoos", "manifest_version": 3})
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: {"daemon_running": True, "profiles": [{"alias": "ctx", "status": "connected"}]},
    )
    _install_proven_binder(monkeypatch, context_id="ctx")
    monkeypatch.setattr(
        "backend.application.setup.environment_snapshot.refresh_component_snapshot",
        lambda state_path: calls.append(state_path) or {},
    )

    response = client.post("/api/setup/verify-extension")

    assert response.status_code == 200
    assert response.json()["status"] == "bound"
    assert calls == [setup_routes.SETUP_STATE_PATH]


def test_post_verify_extension_uses_launcher_proof_with_multiple_connected_contexts(client, monkeypatch):
    monkeypatch.setattr(
        actions, "inspect_opencli_extension",
        lambda: {"loaded": True, "id": "infoos", "manifest_version": 3},
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: {
            "daemon_running": True,
            "daemon_version": "1.8.0",
            "extension_connected": True,
            "doctor_status": "ok",
            "profiles": [
                {"alias": "ctx-personal", "status": "connected", "version": "1.0.20"},
                {"alias": "ctx-other", "status": "connected", "version": "1.0.20"},
            ],
            "last_checked_at": "2026-06-21T00:00:00Z",
            "hint": None,
        },
    )
    binder_calls = _install_proven_binder(monkeypatch, context_id="ctx-dedicated")

    response = client.post("/api/setup/verify-extension")
    assert response.status_code == 200
    assert response.json()["status"] == "bound"
    assert response.json()["context_id"] == "ctx-dedicated"
    assert binder_calls == [True]


def test_post_verify_extension_reports_launcher_binding_failure(client, monkeypatch):
    monkeypatch.setattr(
        actions, "inspect_opencli_extension",
        lambda: {"loaded": True, "id": "infoos", "manifest_version": 3},
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: {
            "daemon_running": True,
            "extension_connected": True,
            "profiles": [],
            "last_checked_at": "2026-06-21T00:00:00Z",
            "hint": None,
        },
    )
    monkeypatch.setattr(
        actions,
        "bind_infoos_chrome",
        lambda: {"ok": False, "error": "No dedicated Browser Bridge context appeared"},
    )

    response = client.post("/api/setup/verify-extension")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["status"] == "binding-failed"
    assert payload["current_step"] != "login_accounts"
    assert "dedicated Browser Bridge" in payload["hint"]


def test_post_verify_extension_does_not_advance_without_persisted_binding(client, monkeypatch):
    monkeypatch.setattr(
        actions,
        "inspect_opencli_extension",
        lambda: {"loaded": True, "id": "infoos", "manifest_version": 3},
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: {"daemon_running": True, "profiles": []},
    )
    monkeypatch.setattr(actions, "bind_infoos_chrome", lambda: {"ok": True})

    response = client.post("/api/setup/verify-extension")

    assert response.status_code == 200
    assert response.json()["status"] == "binding-persistence-failed"
    state = client.get("/api/setup/status").json()
    assert state["current_step"] != "login_accounts"
    assert "bind_profile" not in state["completed_steps"]


def test_post_verify_extension_opencli_missing(client, monkeypatch):
    monkeypatch.setattr(
        actions, "inspect_opencli_extension",
        lambda: {"loaded": True, "id": "infoos", "manifest_version": 3},
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: {"daemon_running": False, "profiles": [], "hint": "opencli daemon not running"},
    )
    monkeypatch.setattr(
        actions,
        "ensure_opencli_daemon",
        lambda: SimpleNamespace(returncode=1, stdout="", stderr="missing"),
    )

    response = client.post("/api/setup/verify-extension")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["status"] == "opencli-missing"


def test_post_verify_extension_starts_missing_daemon_then_binds(client, monkeypatch):
    monkeypatch.setattr(
        actions, "inspect_opencli_extension",
        lambda: {"loaded": True, "id": "infoos", "manifest_version": 3},
    )
    snapshots = iter([
        {"daemon_running": False, "profiles": [], "hint": "opencli daemon not running"},
        {
            "daemon_running": True,
            "extension_connected": True,
            "profiles": [{"alias": "ctx-linux", "status": "connected"}],
            "hint": None,
        },
    ])
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        lambda: next(snapshots),
    )
    daemon_calls: list[bool] = []
    monkeypatch.setattr(
        actions,
        "ensure_opencli_daemon",
        lambda: daemon_calls.append(True) or SimpleNamespace(returncode=0, stdout="", stderr=""),
        raising=False,
    )
    _install_proven_binder(monkeypatch, context_id="ctx-linux")

    response = client.post("/api/setup/verify-extension")

    assert response.status_code == 200
    assert response.json()["status"] == "bound"
    assert response.json()["context_id"] == "ctx-linux"
    assert daemon_calls == [True]


def test_post_verify_extension_no_extension(client, monkeypatch):
    monkeypatch.setattr(
        actions, "inspect_opencli_extension",
        lambda: {"loaded": False, "id": None, "manifest_version": None, "error": "not found"},
    )
    response = client.post("/api/setup/verify-extension")
    payload = response.json()
    assert payload["ok"] is False
    assert payload["status"] == "no-extension"


def test_post_verify_platform_runs_persistence_use_case(client, monkeypatch):
    monkeypatch.setattr(
        actions,
        "verify_login_persistence",
        lambda _path, platform: {
            "ok": True,
            "platform": platform,
            "status": "verified",
            "last_checked": "2026-06-21T00:00:00Z",
            "error": None,
            "persisted": True,
            "persistence_checked_at": "2026-06-21T00:00:00Z",
            "persistence_stage": "verified",
        },
    )

    response = client.post("/api/setup/verify/x")
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "verified"
    assert payload["persisted"] is True
    assert payload["persistence_stage"] == "verified"


def test_post_verify_platform_does_not_call_prepare(client, monkeypatch):
    """probe_platform uses the existing setup binding. The wizard must
    not force a runtime prepare to answer a login question."""
    import backend.application.usecases.browser_session as browser_session_mod
    called = {"prepare": False}

    def forbidden_prepare(*args, **kwargs):
        called["prepare"] = True
        return {"status": "blocked", "issues": ["forbidden"]}

    monkeypatch.setattr(actions, "verify_login_persistence", lambda _path, platform: {
        "ok": False,
        "platform": platform,
        "status": "failed",
        "last_checked": "2026-06-21T00:00:00Z",
        "error": "Dedicated Chrome binding is not verified",
        "persisted": False,
        "persistence_checked_at": "2026-06-21T00:00:00Z",
        "persistence_stage": "precondition",
    })
    monkeypatch.setattr(
        browser_session_mod, "prepare_opencli_browser_session",
        forbidden_prepare,
        raising=False,
    )

    response = client.post("/api/setup/verify/x")
    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert called["prepare"] is False, "wizard must not invoke runtime prepare for login probe"


def _account(status_value):
    from backend.application.setup.state import AccountState, AccountStatus
    return AccountState(status=AccountStatus(status_value), last_checked="2026-06-21T00:00:00Z")


def test_post_first_fetch_submits_durable_fetch_and_waits_for_worker(client, tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "setup_x",
            "platform": "x",
            "source_type": "creator",
            "identifier": "setup-x",
            "display_name": "Setup X",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
        }
    )

    def no_sync_pipeline(*args, **kwargs):
        raise AssertionError("setup route must only submit a durable FETCH task")

    monkeypatch.setattr("backend.application.workflows.run_once.run_once", no_sync_pipeline)
    response = client.post("/api/setup/first-fetch")
    assert response.status_code == 202
    payload = response.json()
    assert payload["ok"] is True
    assert payload["queued"] is True
    assert payload["current_step"] == "fresh"
    assert payload["fetched_count"] == 0
    task_id = payload["batch"]["task_ids"][0]
    task = TaskRepository.for_base_dir(tmp_path).get(task_id)
    assert task is not None
    assert task.task_type == TaskType.FETCH
    assert task.status == TaskStatus.PENDING
    assert task.artifacts_summary["setup_first_fetch"] is True
    assert not (tmp_path / "setup-complete.flag").exists()

    monkeypatch.setattr(
        "backend.infrastructure.runtime.environment.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    monkeypatch.setattr(
        "backend.infrastructure.runtime.environment.setup_complete_flag_path",
        lambda: tmp_path / "setup-complete.flag",
    )
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session",
        lambda **_kwargs: {"status": "ready", "browser_headless": False},
    )
    worker = run_tasks_by_ids(
        tmp_path,
        [task_id],
        handlers={
            TaskType.FETCH: lambda *_args: {
                "status": "success",
                "artifacts": {"fetched_count": 7, "card_build_task_ids": []},
            }
        },
    )
    assert worker["succeeded"] == 1
    state = client.get("/api/setup/status").json()
    assert state["current_step"] == "complete"
    assert state["first_fetch"]["fetched_count"] == 7
    assert (tmp_path / "setup-complete.flag").exists()


def test_post_reset_reopens_first_run_setup(client, tmp_path):
    state_path = tmp_path / "setup-state.json"
    flag_path = tmp_path / "setup-complete.flag"
    state_path.write_text(
        '{"version": 1, "current_step": "complete", "completed_steps": ["first_fetch"], "wizard_opens": 4}',
        encoding="utf-8",
    )
    flag_path.write_text("setup complete\n", encoding="utf-8")

    response = client.post("/api/setup/reset")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_step"] == "fresh"
    assert payload["completed_steps"] == []
    assert payload["wizard_opens"] == 0
    assert not flag_path.exists()
    assert '"current_step": "fresh"' in state_path.read_text(encoding="utf-8")


def test_post_dismiss_marks_setup_skipped(client, tmp_path):
    state_path = tmp_path / "setup-state.json"
    state_path.write_text('{"version": 1, "current_step": "fresh"}', encoding="utf-8")

    response = client.post("/api/setup/dismiss")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_step"] == "skipped"
    assert '"current_step": "skipped"' in state_path.read_text(encoding="utf-8")
