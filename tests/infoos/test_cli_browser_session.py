"""CLI command tests — browser session preflight, status, opencli doctor."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.usecases.browser_session import (
    browser_session_status,
    prepare_opencli_browser_session,
)
from backend.application.usecases.opencli_status import _opencli_status
from backend.application.usecases.source_config import load_source_configs


def _bound_session_state(profile_dir=None, context_id="ctx-infoos"):
    from backend.infrastructure.browser.session_state import BrowserSessionState
    from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR

    return BrowserSessionState(
        schema_version=1,
        profile_dir=str(profile_dir or DEFAULT_INFOOS_CHROME_PROFILE_DIR),
        context_id=context_id,
        alias="infoos-fetch",
        started_at="2026-06-17T00:00:00Z",
        last_health_at="2026-06-17T00:00:00Z",
        last_health_status="ready",
    )

def test_x_home_for_you_example_config_is_browser_backed_opencli_source(tmp_path):
    """The x-home-for-you.example.yaml registry entry resolves to a
    browser-backed opencli source when seeded into SQLite. The DB is the
    single source of truth — the YAML is just a documentation artifact.
    """
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source({
        "source_id": "x_home_for_you",
        "platform": "x",
        "source_type": "creator",
        "identifier": "for-you",
        "connector_id": "opencli_x_home_for_you",
        "cadence_hours": 1.0,
        "enabled": True,
        "raw_metadata": {
            "source_lane": "core",
            "discovery_reason": "X home For You recommendation timeline",
            "viewpoint_hint": "unknown",
            "source_confidence": "medium",
            "topics": ["x_home", "recommendations"],
            "purpose": "collect the logged-in X home recommendation feed",
            "review_policy": "manual_review",
        },
    })

    sources = load_source_configs(tmp_path)

    assert sources == [
        {
            "source_id": "x_home_for_you",
            "connector_id": "opencli_x_home_for_you",
            "enabled": True,
            "requires_browser_session": True,
            "source_lane": "core",
            "discovery_reason": "X home For You recommendation timeline",
            "viewpoint_hint": "unknown",
            "source_confidence": "medium",
            "topics": ["x_home", "recommendations"],
            "purpose": "collect the logged-in X home recommendation feed",
            "review_policy": "manual_review",
            "type": "opencli",
            "executable": "opencli",
            "command": ["twitter", "tweets", "for-you", "--limit", "50"],
            "source_platform": "x",
            "source_type": "social_post",
            "result_order": "newest_first",
            "field_mapping": {
                "id": "id",
                "title": "text",
                "url": "url",
                "text": "text",
                "author_name": "author.name",
                "author_handle": "author.username",
                "published_at": "created_at",
            },
            "metadata_mapping": {
                "views": "views",
                "likes": "likes",
                "replies": "replies",
                "retweets": "retweets",
                "bookmarks": "bookmarks",
                "quotes": "quotes",
            },
        }
    ]


def _seed_browser_session_sources(tmp_path):
    """Seed the opencli sources used by the preflight/CLI tests."""
    from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
    from backend.infrastructure.storage.repositories import SourceRepository

    ensure_workbench_seeded(tmp_path)
    repo = SourceRepository(tmp_path)
    repo.upsert_source({
        "source_id": "opencli_hn",
        "platform": "x",
        "source_type": "creator",
        "identifier": "hackernews_top",
        "connector_id": "opencli_hn",
        "command": ["hackernews", "top", "--limit", "1"],
        "executable": "opencli",
        "cadence_hours": 1.0,
        "enabled": True,
    })
    repo.upsert_source({
        "source_id": "opencli_x",
        "platform": "x",
        "source_type": "creator",
        "identifier": "InfoOS",
        "connector_id": "opencli_x",
        "command": ["twitter", "search", "InfoOS", "--limit", "1"],
        "executable": "opencli",
        "cadence_hours": 1.0,
        "enabled": True,
        "raw_metadata": {"requires_browser_session": True},
    })
    repo.upsert_source({
        "source_id": "opencli_xhs_disabled",
        "platform": "x",
        "source_type": "creator",
        "identifier": "InfoOS",
        "connector_id": "opencli_xhs_disabled",
        "command": ["xiaohongshu", "search", "InfoOS", "--limit", "1"],
        "executable": "opencli",
        "cadence_hours": 1.0,
        "enabled": False,
        "raw_metadata": {"requires_browser_session": True},
    })


def test_opencli_status_resolves_stable_env_executable(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == ["/stable/bin/opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["/stable/bin/opencli", "daemon", "status"]:
            return SimpleNamespace(
                stdout="Daemon: running (PID 123)\nVersion: v1.8.0\nExtension: connected (v1.0.15)\n",
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/stable/bin/opencli")
    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)

    result = _opencli_status("opencli")

    assert result["executable"] == "/stable/bin/opencli"
    assert result["version"] == "1.8.0"
    assert result["daemon_version"] == "1.8.0"
    assert result["browser_bridge_connected"] is True
    assert calls == [
        ["/stable/bin/opencli", "--version"],
        ["/stable/bin/opencli", "daemon", "status"],
    ]


def test_browser_session_status_requires_dedicated_infoos_profile(monkeypatch):
    import backend.application.usecases.browser_session as browser_session_module
    from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR

    def fail_opencli_status(executable):
        raise AssertionError(f"unexpected OpenCLI status probe: {executable}")

    monkeypatch.setattr("backend.application.usecases.browser_session._opencli_status", fail_opencli_status)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_running", lambda: False)
    monkeypatch.setattr(
        browser_session_module,
        "session_state",
        SimpleNamespace(read=lambda: _bound_session_state(), is_alive=lambda state: True),
    )

    result = browser_session_status()

    assert result["status"] == "blocked"
    assert result["opencli"]["doctor_status"] == "not_checked"
    assert result["profile"]["running"] is False
    assert result["profile"]["required"] == str(DEFAULT_INFOOS_CHROME_PROFILE_DIR)
    assert result["profile"]["binding_status"] == "not_running"


def test_browser_session_status_without_session_does_not_touch_opencli(monkeypatch):
    import backend.application.usecases.browser_session as browser_session_module

    def fail_opencli_status(executable):
        raise AssertionError(f"unexpected OpenCLI status probe: {executable}")

    monkeypatch.setattr("backend.application.usecases.browser_session._opencli_status", fail_opencli_status)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_running", lambda: False)
    monkeypatch.setattr(
        browser_session_module,
        "session_state",
        SimpleNamespace(read=lambda: None, is_alive=lambda state: False),
    )

    result = browser_session_status()

    assert result["status"] == "blocked"
    assert result["daemon"]["running"] is False
    assert result["extension"]["connected"] is False
    assert result["opencli"]["doctor_status"] == "not_configured"
    assert result["profile"]["binding_status"] == "not_configured"


def test_browser_session_status_reports_configured_opencli_without_probing(monkeypatch):
    import backend.application.usecases.browser_session as browser_session_module

    configured = "/var/lib/infoos/state/runtime/opencli/node_modules/.bin/opencli"
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", configured)
    monkeypatch.setattr(
        "backend.application.usecases.browser_session._infoos_chrome_profile_running",
        lambda: False,
    )
    monkeypatch.setattr(
        browser_session_module,
        "session_state",
        SimpleNamespace(read=lambda: None, is_alive=lambda state: False),
    )

    result = browser_session_status()

    assert result["opencli"]["executable"] == configured
    assert result["opencli"]["configured_executable"] == configured


def test_browser_session_status_blocks_generic_bridge_without_bound_session(monkeypatch):
    import backend.application.usecases.browser_session as browser_session_module

    monkeypatch.setattr(
        "backend.application.usecases.browser_session._opencli_status",
        lambda executable: {
            "executable": executable,
            "available": True,
            "version": "1.8.0",
            "browser_bridge_connected": True,
            "daemon_running": True,
            "doctor_status": "ok",
            "doctor_output": "[OK] Connectivity",
            "doctor_hint": None,
        },
    )
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_running", lambda: True)
    monkeypatch.setattr(
        browser_session_module,
        "session_state",
        SimpleNamespace(read=lambda: None, is_alive=lambda state: False),
    )

    result = browser_session_status()

    assert result["status"] == "blocked"
    assert result["profile"]["running"] is True
    assert result["profile"]["bound"] is False
    assert result["profile"]["binding_status"] == "not_configured"


def test_browser_session_status_probe_flag_is_passive_when_idle(monkeypatch, isolate_opencli_lock):
    events = []

    class FakeLock:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, exc_type, exc, tb):
            events.append("exit")

    def fail_opencli_status(executable):
        raise AssertionError(f"unexpected OpenCLI status probe: {executable}")

    monkeypatch.setattr("backend.application.usecases.browser_session._opencli_status", fail_opencli_status)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_running", lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session.opencli_browser_lock", lambda: FakeLock())
    import backend.application.usecases.browser_session as browser_session_module

    monkeypatch.setattr(
        browser_session_module,
        "session_state",
        SimpleNamespace(read=lambda: _bound_session_state(), is_alive=lambda state: True),
    )

    def fake_run(command, **kwargs):
        events.append(("run", command))
        if command == ["opencli", "profile", "list"]:
            return SimpleNamespace(stdout="infoos-fetch — connected v1.0.2\n", stderr="", returncode=0)
        return SimpleNamespace(stdout="[]", stderr="", returncode=0)

    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)

    result = browser_session_status(probe_x_login=True)

    assert result["status"] == "ready"
    assert result["opencli"]["doctor_status"] == "not_checked"
    assert result["x_login"]["status"] == "unknown"
    assert result["profile"]["bound"] is True
    assert result["profile"]["binding_status"] == "ready"
    assert events == []
