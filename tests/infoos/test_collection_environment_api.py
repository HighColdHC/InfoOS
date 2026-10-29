from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import collection_environment
from backend.app.main import create_app
from backend.infrastructure.runtime.setup_state import (
    AccountState,
    AccountStatus,
    SetupState,
    save_state,
)
from backend.infrastructure.browser import session_state
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository


def test_collection_environment_identifies_rss_source_circuit(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "rss-A", "platform": "rss", "source_type": "feed",
        "identifier": "https://example.com/feed", "display_name": "测试订阅 A",
        "cadence_hours": 24, "enabled": True, "connector_status": "supported",
    })
    circuits = RuntimeCircuitRepository(tmp_path)
    for key in ("rss:rss-A", "rss:deleted-source"):
        circuits.record_failure("platform", key, occurred_at="2026-10-20T00:00:00Z",
                                policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=60))
    response = client.get("/api/collection-environment")
    assert response.status_code == 200
    payload = response.json()
    rows = {row["scope_key"]: row for row in payload["runtime_circuits"]}
    assert rows["rss:rss-A"]["scope_kind"] == "platform"
    assert rows["rss:rss-A"]["source_id"] == "rss-A"
    assert rows["rss:rss-A"]["source_name"] == "测试订阅 A"
    assert rows["rss:deleted-source"]["source_name"] == "deleted-source"
    assert payload["pending_auth_recoveries"] == []


def _client(tmp_path, monkeypatch) -> TestClient:
    state_path = tmp_path / "setup-state.json"
    monkeypatch.setenv("INFOOS_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(collection_environment, "SETUP_STATE_PATH", state_path)
    monkeypatch.setattr(session_state, "JSON_PATH", tmp_path / "browser-session.json")
    session_state._cache_clear()
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(collection_environment.router)
    return TestClient(app)


def test_get_collection_environment_returns_saved_seven_components_and_six_accounts(
    tmp_path,
    monkeypatch,
):
    client = _client(tmp_path, monkeypatch)
    state = SetupState.from_dict({})
    state.component_snapshot_at = "2026-07-16T08:00:00Z"
    state.components["dedicated_chrome"].status = "installed"
    state.components["dedicated_chrome"].version = "126.0"
    state.accounts["x"] = AccountState(
        status=AccountStatus.NEEDS_LOGIN,
        last_checked="2026-07-16T08:30:00Z",
        error="No login cookie",
    )
    save_state(tmp_path / "setup-state.json", state)
    SourceRepository(tmp_path).save_configuration(
        {
            "source_id": "source-x-1",
            "platform": "x",
            "source_type": "creator",
            "identifier": "alice",
            "display_name": "Alice",
            "cadence_hours": 1,
            "enabled": True,
        },
        change_reason="created",
    )

    response = client.get("/api/collection-environment")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_health"]["status"] in {"unknown", "healthy", "unhealthy"}
    assert payload["snapshot_at"] == "2026-07-16T08:00:00Z"
    assert len(payload["components"]) == 7
    assert [item["id"] for item in payload["components"]] == [
        "dedicated_chrome",
        "opencli_bridge",
        "yt_dlp",
        "gallery_dl",
        "ffmpeg",
        "local_asr",
        "sqlite",
    ]
    assert payload["components"][0] == {
        "id": "dedicated_chrome",
        "name": "专用 Chrome",
        "status": "installed",
        "version": "126.0",
        "is_chrome": True,
        "action": "launch",
        "last_checked_at": None,
        "detail_code": None,
    }
    assert len(payload["accounts"]) == 6
    assert [item["platform"] for item in payload["accounts"]] == [
        "x", "youtube", "reddit", "xiaohongshu", "douyin", "zsxq",
    ]
    x_account = payload["accounts"][0]
    assert x_account["status"] == "needs_login"
    assert x_account["last_checked_at"] == "2026-07-16T08:30:00Z"
    assert x_account["affected_source_count"] == 1
    assert x_account["error"] == "No login cookie"


def test_get_collection_environment_is_strictly_passive(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    save_state(tmp_path / "setup-state.json", SetupState.from_dict({}))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("active probe called by passive GET")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
    monkeypatch.setattr("shutil.which", forbidden)
    monkeypatch.setattr("importlib.metadata.version", forbidden)
    monkeypatch.setattr(
        "backend.infrastructure.browser.cookie_status.check_cookie_login",
        forbidden,
    )
    monkeypatch.setattr(
        "backend.infrastructure.browser.opencli_health.refresh",
        forbidden,
    )
    monkeypatch.setattr(
        "backend.infrastructure.runtime.media_tools.media_tool_status",
        forbidden,
    )

    response = client.get("/api/collection-environment")

    assert response.status_code == 200
    assert all(item["status"] == "unknown" for item in response.json()["components"])


def test_collection_environment_counts_only_sources_that_require_login(
    tmp_path,
    monkeypatch,
):
    client = _client(tmp_path, monkeypatch)
    save_state(tmp_path / "setup-state.json", SetupState.from_dict({}))
    repository = SourceRepository(tmp_path)
    for source_id, source_type, identifier in (
        ("youtube-channel", "channel", "@openai"),
        ("youtube-keyword", "keyword", "agentic AI"),
        ("youtube-home", "home_feed", "recommended"),
    ):
        repository.save_configuration(
            {
                "source_id": source_id,
                "platform": "youtube",
                "source_type": source_type,
                "identifier": identifier,
                "display_name": source_id,
                "cadence_hours": 6,
                "enabled": True,
            },
            change_reason="created",
        )

    response = client.get("/api/collection-environment")

    youtube = next(
        item for item in response.json()["accounts"] if item["platform"] == "youtube"
    )
    assert youtube["affected_source_count"] == 1


def test_get_collection_environment_projects_container_failure(tmp_path, monkeypatch):
    health_path = tmp_path / "container-health.json"
    health_path.write_text(
        '{"status":"unhealthy","reasons":["worker unavailable"],"checked_at":"2026-07-22T12:00:00Z"}'
    )
    monkeypatch.setenv("INFOOS_CONTAINER_HEALTH_PATH", str(health_path))
    client = _client(tmp_path, monkeypatch)
    save_state(tmp_path / "setup-state.json", SetupState.from_dict({}))

    response = client.get("/api/collection-environment")

    assert response.json()["runtime_health"] == {
        "status": "unhealthy",
        "reasons": ["worker unavailable"],
        "checked_at": "2026-07-22T12:00:00Z",
    }


def test_verified_account_without_bridge_binding_keeps_login_fact_and_guides_setup(
    tmp_path,
    monkeypatch,
):
    client = _client(tmp_path, monkeypatch)
    state = SetupState.from_dict({})
    state.accounts["x"] = AccountState(
        status=AccountStatus.VERIFIED,
        last_checked="2026-07-22T00:00:00Z",
    )
    save_state(tmp_path / "setup-state.json", state)

    response = client.get("/api/collection-environment")

    assert response.status_code == 200
    account = next(item for item in response.json()["accounts"] if item["platform"] == "x")
    assert account["status"] == "verified"
    assert account["session_id"] is None
    assert account["action_hint"] == "已登录；请运行首次设置绑定 OpenCLI Bridge"


def test_collection_environment_is_registered_on_product_app(tmp_path, monkeypatch):
    monkeypatch.setattr(collection_environment, "SETUP_STATE_PATH", tmp_path / "setup-state.json")
    save_state(tmp_path / "setup-state.json", SetupState.from_dict({}))

    response = TestClient(create_app(tmp_path)).get("/api/collection-environment")

    assert response.status_code == 200
    assert len(response.json()["components"]) == 7


def test_collection_environment_projects_only_safe_passive_circuit_fields(
    tmp_path, monkeypatch
):
    from backend.infrastructure.storage.runtime_circuits import (
        CircuitPolicy,
        RuntimeCircuitRepository,
    )

    client = _client(tmp_path, monkeypatch)
    save_state(tmp_path / "setup-state.json", SetupState.from_dict({}))
    RuntimeCircuitRepository(tmp_path).record_failure(
        "platform",
        "x",
        occurred_at="2026-07-23T00:00:00Z",
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=60),
    )

    payload = client.get("/api/collection-environment").json()

    assert payload["runtime_circuits"] == [
        {
            "scope_kind": "platform",
            "scope_key": "x",
            "state": "open",
            "outage_epoch": 1,
            "consecutive_failures": 1,
            "opened_at": "2026-07-23T00:00:00Z",
            "next_probe_at": "2026-07-23T00:01:00Z",
            "reason_code": "upstream_unavailable",
            "last_failure_at": "2026-07-23T00:00:00Z",
            "last_success_at": None,
            "user_action": "none",
            "updated_at": "2026-07-23T00:00:00Z",
        }
    ]
    assert "probe_owner_token" not in str(payload)
    assert "runtime_resource_leases" not in payload
