"""CLI command tests — prepare-browser-session, restart-opencli-daemon, Chrome launcher."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.usecases.browser_session import (
    prepare_opencli_browser_session,
)
from backend.application.usecases.opencli_status import (
    _infoos_chrome_profile_headless,
    _infoos_chrome_profile_pids,
    _infoos_chrome_profile_running,
)
from backend.application.usecases.browser_session_x import (
    _restart_opencli_daemon,
)
from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR

from tests.infoos._factories import normalize_opencli_command


# Realistic `opencli twitter profile` response for a logged-in user.
# Used by tests that previously mocked `twitter tweets` returning "[success]"
# (which only "passed" because the old probe treated any rc=0 as logged-in).
_LOGGED_IN_X_PROFILE = '{"ok":true,"screen_name":"me","id":"12345"}\n'


@pytest.fixture(autouse=True)
def _use_literal_opencli_in_prepare_browser_tests(monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "opencli")


@pytest.fixture(autouse=True)
def _isolate_browser_session_cache():
    from backend.infrastructure.browser import session_state

    session_state._cache_clear()
    yield
    session_state._cache_clear()


@pytest.fixture(autouse=True)
def _assume_existing_infoos_chrome_is_headless(monkeypatch):
    monkeypatch.setattr(
        "backend.application.usecases.browser_session._infoos_chrome_profile_headless",
        lambda *args, **kwargs: True,
    )


@pytest.fixture(autouse=True)
def _skip_page_settle_delay(monkeypatch):
    # These tests exercise session preparation with mocked browser commands;
    # elapsed page-settle time is not part of their assertions.
    monkeypatch.setattr(
        "backend.application.usecases.browser_session._wait_for_page_settle",
        lambda: None,
    )


def _patch_profile_running(monkeypatch, replacement):
    for module in ("browser_session", "browser_session_profile", "browser_session_x"):
        monkeypatch.setattr(
            f"backend.application.usecases.{module}._infoos_chrome_profile_running",
            replacement,
        )


def _patch_profile_pids(monkeypatch, replacement):
    for module in ("opencli_status", "browser_session_profile", "browser_session_x"):
        monkeypatch.setattr(
            f"backend.application.usecases.{module}._infoos_chrome_profile_pids",
            replacement,
        )


def _isolate_browser_session_json(monkeypatch, tmp_path):
    from backend.infrastructure.browser import session_state

    json_path = tmp_path / "browser-session.json"
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    session_state._cache_clear()
    return json_path

def test_prepare_opencli_browser_session_blocks_without_dedicated_launcher(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.7.6\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="Daemon: running (PID 123)\nExtension: disconnected\n", stderr="", returncode=0)
        if command[0] == "opencli" and command[1] == "twitter":
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=tmp_path / "missing-opencli-infoos-chrome",
    )

    assert result["status"] == "blocked"
    assert result["browser_opened"] is False
    assert result["browser_bridge_connected"] is False
    assert any(issue == "专用浏览器启动器不存在" for issue in result["issues"])
    assert ["open", "-a", "Google Chrome", "https://x.com/home"] not in calls


def test_prepare_opencli_browser_session_prefers_env_launcher(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []
    default_launcher = tmp_path / "missing-installed-opencli-infoos-chrome"
    env_launcher = tmp_path / "opencli-infoos-chrome"
    env_launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    env_launcher.chmod(0o755)
    monkeypatch.setenv("INFOOS_BROWSER_LAUNCHER", str(env_launcher))
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setattr(
        "backend.application.usecases.browser_session.DEFAULT_INFOOS_BROWSER_LAUNCHER",
        default_launcher,
    )
    _isolate_browser_session_json(monkeypatch, tmp_path)

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(env_launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="Daemon: running (PID 123)\nExtension: connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "ctx-infoos — connected v1.0.2\n" if launcher_has_run else ""
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command in (
            [str(env_launcher), "https://x.com/home", "--headless"],
            [str(env_launcher), "--restart", "https://x.com/home", "--headless"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        probe_x_login=False,
    )

    assert result["status"] == "ready"
    assert result["browser_launcher"] == str(env_launcher)
    assert any(call and call[0] == str(env_launcher) for call in calls)
    assert not any(call and call[0] == str(default_launcher) for call in calls)


def test_prepare_opencli_browser_session_prefers_infoos_launcher(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
    _isolate_browser_session_json(monkeypatch, tmp_path)

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.7.6\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            status_calls = sum(
                1 for call in calls if call == ["opencli", "daemon", "status"]
            )
            if status_calls == 1:
                return SimpleNamespace(stdout="[FAIL] Connectivity: Browser Bridge extension not connected\n", stderr="", returncode=0)
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "ctx-infoos — connected v1.0.2\n" if launcher_has_run else ""
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command in (
            [str(launcher), "https://x.com/home", "--headless"],
            [str(launcher), "--restart", "https://x.com/home", "--headless"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[0] == "opencli" and command[1] == "twitter":
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert result["status"] == "ready"
    assert result["browser_opened"] is True
    assert result["browser_launch_method"] == "launcher"
    assert result["browser_launcher"] == str(launcher)
    assert result["browser_headless"] is True
    assert any(call[0] == str(launcher) and "--headless" in call for call in calls)
    assert ["open", "-a", "Google Chrome", "https://x.com/home"] not in calls
    assert ["opencli", "doctor"] not in calls


def test_prepare_opencli_browser_session_passes_headless_when_runtime_config_enabled(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    config_path = tmp_path / "infoos-config.yaml"
    config_path.write_text("browser:\n  headless_during_fetch: true\n", encoding="utf-8")
    _isolate_browser_session_json(monkeypatch, tmp_path)

    # `read_headless_during_fetch()` resolves via `INFOOS_CONFIG_PATH` env var
    # (not via the legacy _shared.DEFAULT_INFOOS_CONFIG_PATH constant).
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(config_path))

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            status_calls = sum(
                1 for call in calls if call == ["opencli", "daemon", "status"]
            )
            if status_calls == 1:
                return SimpleNamespace(stdout="[FAIL] Connectivity: Browser Bridge extension not connected\n", stderr="", returncode=0)
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "ctx-infoos — connected v1.0.2\n" if launcher_has_run else ""
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command in (
            [str(launcher), "https://x.com/home", "--headless"],
            [str(launcher), "--restart", "https://x.com/home", "--headless"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ["opencli", "twitter", "profile"]:
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert result["status"] == "ready"
    assert result["browser_headless"] is True
    assert any(call[0] == str(launcher) and "--headless" in call for call in calls)


def test_prepare_opencli_browser_session_uses_visible_window_when_runtime_config_disabled(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    config_path = tmp_path / "infoos-config.yaml"
    config_path.write_text("browser:\n  headless_during_fetch: false\n", encoding="utf-8")
    _isolate_browser_session_json(monkeypatch, tmp_path)
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(config_path))

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            status_calls = sum(
                1 for call in calls if call == ["opencli", "daemon", "status"]
            )
            if status_calls == 1:
                return SimpleNamespace(stdout="[FAIL] Connectivity: Browser Bridge extension not connected\n", stderr="", returncode=0)
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "ctx-infoos — connected v1.0.2\n" if launcher_has_run else ""
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command in (
            [str(launcher), "https://x.com/home"],
            [str(launcher), "--restart", "https://x.com/home"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ["opencli", "twitter", "profile"]:
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert result["status"] == "ready"
    assert result["browser_headless"] is False
    assert [str(launcher), "https://x.com/home"] in calls
    assert not any(call and call[0] == str(launcher) and "--headless" in call for call in calls)


def test_prepare_opencli_browser_session_restarts_headless_profile_when_visible_fetch_enabled(monkeypatch, isolate_opencli_lock, tmp_path):
    from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR
    from backend.infrastructure.browser import session_state
    from backend.infrastructure.browser.session_state import BrowserSessionState

    calls = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    config_path = tmp_path / "infoos-config.yaml"
    config_path.write_text("browser:\n  headless_during_fetch: false\n", encoding="utf-8")
    json_path = _isolate_browser_session_json(monkeypatch, tmp_path)
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(config_path))
    session_state.write(BrowserSessionState(
        schema_version=1,
        profile_dir=str(DEFAULT_INFOOS_CHROME_PROFILE_DIR),
        context_id="ctx-infoos",
        alias="infoos-fetch",
        started_at="2026-06-23T00:00:00Z",
        last_health_at="2026-06-23T00:00:00Z",
        last_health_status="ready",
    ))

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            return SimpleNamespace(stdout="ctx-infoos infoos-fetch default — connected v1.0.2\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == [str(launcher), "--restart", "https://x.com/home"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ["opencli", "twitter", "profile"]:
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._kill_infoos_chrome", lambda: calls.append(["kill-infoos-chrome"]))
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert json_path.exists()
    assert result["status"] == "ready"
    assert result["browser_headless"] is False
    assert ["kill-infoos-chrome"] in calls
    assert [str(launcher), "--restart", "https://x.com/home"] in calls
    assert not any(call and call[0] == str(launcher) and "--headless" in call for call in calls)


def test_prepare_opencli_browser_session_waits_for_dedicated_profile_when_bridge_is_already_connected(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []
    profile_checks = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
    _isolate_browser_session_json(monkeypatch, tmp_path)

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "ctx-infoos — connected v1.0.2\n" if launcher_has_run else ""
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command in (
            [str(launcher), "https://x.com/home", "--headless"],
            [str(launcher), "--restart", "https://x.com/home", "--headless"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[0] == "opencli" and command[1] == "twitter":
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    def fake_profile_running():
        profile_checks.append(True)
        return len(profile_checks) >= 4

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, fake_profile_running)
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert result["status"] == "ready"
    assert result["profile"]["running"] is True
    assert result["browser_headless"] is True
    assert [str(launcher), "https://x.com/home", "--headless"] in calls


def test_prepare_opencli_browser_session_reuses_visible_profile_when_fetch_headless_enabled(monkeypatch, isolate_opencli_lock, tmp_path):
    from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR
    from backend.infrastructure.browser import session_state
    from backend.infrastructure.browser.session_state import BrowserSessionState

    calls = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    config_path = tmp_path / "infoos-config.yaml"
    config_path.write_text("browser:\n  headless_during_fetch: true\n", encoding="utf-8")
    json_path = _isolate_browser_session_json(monkeypatch, tmp_path)
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(config_path))
    session_state.write(BrowserSessionState(
        schema_version=1,
        profile_dir=str(DEFAULT_INFOOS_CHROME_PROFILE_DIR),
        context_id="ctx-infoos",
        alias="infoos-fetch",
        started_at="2026-06-23T00:00:00Z",
        last_health_at="2026-06-23T00:00:00Z",
        last_health_status="ready",
    ))

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            return SimpleNamespace(stdout="ctx-infoos infoos-fetch default — connected v1.0.2\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ["opencli", "twitter", "profile"]:
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    monkeypatch.setattr("backend.application.usecases.browser_session._kill_infoos_chrome", lambda: calls.append(["kill-infoos-chrome"]))
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert json_path.exists()
    assert result["status"] == "ready"
    assert result["browser_headless"] is False
    assert ["kill-infoos-chrome"] not in calls
    assert not any(call and call[0] == str(launcher) for call in calls)


def test_prepare_opencli_browser_session_rebinds_connected_daily_bridge_to_dedicated_profile(monkeypatch, isolate_opencli_lock, tmp_path):
    from backend.infrastructure.browser import session_state

    calls = []
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    json_path = tmp_path / "browser-session.json"
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    session_state._cache_clear()

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "daily — connected v1.0.2\n"
            if launcher_has_run:
                stdout += "ctx-infoos — connected v1.0.2\n"
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[0] == str(launcher):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ["opencli", "twitter", "profile"]:
            return SimpleNamespace(stdout=_LOGGED_IN_X_PROFILE, stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(wait_seconds=2, poll_interval_seconds=1, browser_launcher=launcher)

    assert result["status"] == "ready"
    assert result["profile"]["bound"] is True
    assert result["profile"]["context_id"] == "ctx-infoos"
    assert ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"] in calls
    assert json_path.exists()


def test_prepare_opencli_browser_session_does_not_bind_preexisting_infoos_alias(monkeypatch, isolate_opencli_lock, tmp_path):
    from backend.infrastructure.browser import session_state

    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    json_path = tmp_path / "browser-session.json"
    monkeypatch.setattr(session_state, "JSON_PATH", json_path)
    session_state._cache_clear()

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command in (
            ["opencli", "daemon", "stop"],
            ["opencli", "daemon", "restart"],
            ["pkill", "-f", "chrome.*chrome-profile"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            return SimpleNamespace(stdout="infoos-fetch — connected v1.0.2\n", stderr="", returncode=0)
        if command[0] == str(launcher):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(wait_seconds=1, poll_interval_seconds=1, browser_launcher=launcher)

    assert result["status"] == "blocked"
    assert result["profile"]["bound"] is False
    assert json_path.exists() is False


def test_prepare_opencli_browser_session_blocks_when_x_probe_fails(monkeypatch, isolate_opencli_lock, tmp_path):
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    calls = []
    _isolate_browser_session_json(monkeypatch, tmp_path)
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "opencli")

    def fake_run(command, **kwargs):
        command = normalize_opencli_command(command)
        calls.append(command)
        launcher_has_run = any(call and call[0] == str(launcher) for call in calls)
        if command == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if command == ["opencli", "daemon", "status"]:
            return SimpleNamespace(stdout="[OK] Connectivity: Browser Bridge extension connected\n", stderr="", returncode=0)
        if command == ["opencli", "profile", "list"]:
            stdout = "ctx-infoos — connected v1.0.2\n" if launcher_has_run else ""
            return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
        if command == ["opencli", "profile", "rename", "ctx-infoos", "infoos-fetch"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command == ["opencli", "profile", "use", "ctx-infoos"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command in (
            [str(launcher), "https://x.com/home", "--headless"],
            [str(launcher), "--restart", "https://x.com/home", "--headless"],
        ):
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        if command[:3] == ["opencli", "twitter", "profile"]:
            return SimpleNamespace(
                stdout='{"ok":false,"error":{"code":"AUTH_REQUIRED","message":"Not logged into x.com (no ct0 cookie)"}}\n',
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr("backend.application.usecases.browser_session._infoos_chrome_profile_headless", lambda: False)
    _patch_profile_pids(monkeypatch, lambda: [])
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = prepare_opencli_browser_session(
        wait_seconds=2,
        poll_interval_seconds=1,
        browser_launcher=launcher,
    )

    assert result["status"] == "blocked"
    assert result["x_login"]["status"] == "logged_out"
    assert any("X 登录状态检查未通过" in issue for issue in result["issues"])

def test_restart_opencli_daemon_prefers_restart_command(monkeypatch, isolate_opencli_lock, tmp_path):
    calls = []
    popen_calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def fake_popen(command, **kwargs):
        popen_calls.append(command)
        return SimpleNamespace(pid=123)

    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/stable/bin/opencli")
    monkeypatch.setattr("backend.application.usecases.browser_session_x.subprocess.run", fake_run)
    monkeypatch.setattr("backend.application.usecases.browser_session_x.subprocess.Popen", fake_popen)
    monkeypatch.setattr("backend.application.usecases.browser_session_x.Path.home", lambda: tmp_path)
    monkeypatch.setattr("backend.application.usecases.browser_session_x.time.sleep", lambda seconds: None)

    result = {"issues": []}
    _restart_opencli_daemon("opencli", result)

    assert calls == [
        ["/stable/bin/opencli", "daemon", "stop"],
        ["/stable/bin/opencli", "daemon", "restart"],
    ]
    assert popen_calls == []
    assert result["issues"] == []

@pytest.fixture(params=[
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/opt/google/chrome/chrome",
], ids=["macos", "linux"])
def owned_chrome_binary(monkeypatch, request):
    from backend.application.usecases import opencli_status

    monkeypatch.setattr(opencli_status, "DEFAULT_INFOOS_CHROME_BIN", Path(request.param))
    # The subprocess responses use a synthetic PID, not a process in host /proc.
    monkeypatch.setattr(opencli_status, "_pid_owned_by_current_user", lambda pid: pid == 456)
    monkeypatch.setattr(opencli_status, "pid_namespace_matches_current", lambda pid: pid == 456)
    return request.param


def test_infoos_chrome_profile_running_uses_pgrep_option_separator(monkeypatch, owned_chrome_binary):
    calls = []
    profile_arg = f"--user-data-dir={DEFAULT_INFOOS_CHROME_PROFILE_DIR}"

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == [
            "/usr/bin/pgrep",
            "-f",
            "--",
            profile_arg,
        ]:
            return SimpleNamespace(stdout="123\n456\n", stderr="", returncode=0)
        if command == ["/bin/ps", "-p", "123", "-o", "command="]:
            return SimpleNamespace(
                stdout=f"/bin/zsh -lc pgrep {profile_arg}\n",
                stderr="",
                returncode=0,
            )
        if command == ["/bin/ps", "-p", "456", "-o", "command="]:
            return SimpleNamespace(
                stdout=(
                    f"{owned_chrome_binary} {profile_arg} "
                    "--new-window https://x.com/home\n"
                ),
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)

    assert _infoos_chrome_profile_running() is True
    assert calls[0] == [
        "/usr/bin/pgrep",
        "-f",
        "--",
        profile_arg,
    ]


def test_infoos_chrome_profile_pids_rejects_foreign_pid_namespace(monkeypatch):
    profile_arg = f"--user-data-dir={DEFAULT_INFOOS_CHROME_PROFILE_DIR}"

    def fake_run(command, **kwargs):
        if command == ["/usr/bin/pgrep", "-f", "--", profile_arg]:
            return SimpleNamespace(stdout="456\n789\n", stderr="", returncode=0)
        if command[:3] == ["/bin/ps", "-p", "456"]:
            return SimpleNamespace(
                stdout=(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                    f"{profile_arg} https://x.com/home\n"
                ),
                stderr="",
                returncode=0,
            )
        if command[:3] == ["/bin/ps", "-p", "789"]:
            return SimpleNamespace(
                stdout=(
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                    f"{profile_arg} https://x.com/home\n"
                ),
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.application.usecases.opencli_status._pid_owned_by_current_user",
        lambda pid: True,
    )
    monkeypatch.setattr(
        "backend.application.usecases.opencli_status.pid_namespace_matches_current",
        lambda pid: pid == 456,
        raising=False,
    )

    assert _infoos_chrome_profile_pids() == [456]


def test_infoos_chrome_profile_running_rejects_non_chrome_process_match(monkeypatch):
    profile_arg = f"--user-data-dir={DEFAULT_INFOOS_CHROME_PROFILE_DIR}"

    def fake_run(command, **kwargs):
        if command == [
            "/usr/bin/pgrep",
            "-f",
            "--",
            profile_arg,
        ]:
            return SimpleNamespace(stdout="123\n", stderr="", returncode=0)
        if command == ["/bin/ps", "-p", "123", "-o", "command="]:
            return SimpleNamespace(
                stdout=f"/bin/zsh -lc /Applications/Google Chrome.app/Contents/MacOS/Google Chrome {profile_arg}\n",
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)

    assert _infoos_chrome_profile_running() is False

def test_infoos_chrome_profile_running_rejects_chrome_helper_process(monkeypatch):
    profile_arg = f"--user-data-dir={DEFAULT_INFOOS_CHROME_PROFILE_DIR}"

    def fake_run(command, **kwargs):
        if command == [
            "/usr/bin/pgrep",
            "-f",
            "--",
            profile_arg,
        ]:
            return SimpleNamespace(stdout="456\n", stderr="", returncode=0)
        if command == ["/bin/ps", "-p", "456", "-o", "command="]:
            return SimpleNamespace(
                stdout=(
                    "/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/"
                    "Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper "
                    f"{profile_arg}\n"
                ),
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)

    assert _infoos_chrome_profile_running() is False

def test_infoos_chrome_profile_headless_detects_process_flag(monkeypatch, owned_chrome_binary):
    profile_arg = f"--user-data-dir={DEFAULT_INFOOS_CHROME_PROFILE_DIR}"

    def fake_run(command, **kwargs):
        if command == [
            "/usr/bin/pgrep",
            "-f",
            "--",
            profile_arg,
        ]:
            return SimpleNamespace(stdout="456\n", stderr="", returncode=0)
        if command == ["/bin/ps", "-p", "456", "-o", "command="]:
            return SimpleNamespace(
                stdout=(
                    f"{owned_chrome_binary} "
                    f"{profile_arg} --headless=new https://x.com/home\n"
                ),
                stderr="",
                returncode=0,
            )
        raise AssertionError(command)

    monkeypatch.setattr("backend.application.usecases.opencli_status.subprocess.run", fake_run)

    assert _infoos_chrome_profile_headless() is True
