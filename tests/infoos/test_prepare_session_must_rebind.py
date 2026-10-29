"""prepare_opencli_browser_session must_rebind short-circuit.

When the setup wizard verifies a platform (X / YouTube), the user is
actively using the dedicated InfoOS Chrome. The verify path must not
relaunch Chrome (it would interrupt the user). It uses
``must_rebind=False`` to skip the repair escalation.

The runtime fetch path (scheduler, web routes/runs.py) uses the
default ``must_rebind=True`` because a stale binding is a hard
failure there.
"""
from __future__ import annotations

import os
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.usecases import browser_session  # noqa: E402
from backend.application.usecases.browser_session import prepare_opencli_browser_session  # noqa: E402
from backend.application.usecases import browser_session_profile  # noqa: E402
from backend.infrastructure.browser.session_state import BrowserSessionState  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_browser_session_cache():
    browser_session.session_state._cache_clear()
    yield
    browser_session.session_state._cache_clear()


def _make_launcher(tmp_path: Path) -> Path:
    launcher = tmp_path / "opencli-infoos-chrome"
    launcher.write_text("#!/bin/zsh\necho stub\n", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _fake_opencli_healthy(executable: str = "opencli") -> dict:
    return {
        "executable": executable,
        "configured_executable": executable,
        "available": True,
        "version": "1.8.0",
        "daemon_version": "1.8.0",
        "browser_bridge_connected": True,
        "daemon_running": True,
        "doctor_status": "ok",
        "doctor_output": None,
        "doctor_hint": None,
    }


def _patch_profile_running(monkeypatch, replacement):
    for module in ("browser_session", "browser_session_profile", "browser_session_x"):
        monkeypatch.setattr(
            f"backend.application.usecases.{module}._infoos_chrome_profile_running",
            replacement,
        )


def test_must_rebind_false_skips_launcher_when_binding_alive(tmp_path, monkeypatch):
    """must_rebind=False + binding alive = return ready without touching Chrome."""
    launcher = _make_launcher(tmp_path)
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    # Track launcher invocations: subprocess.run on the launcher must NOT happen.
    launcher_invocations: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        launcher_invocations.append(cmd)
        # Anything that would touch the launcher should be a test failure.
        if cmd and str(cmd[0]).endswith("opencli-infoos-chrome"):
            raise AssertionError(
                f"must_rebind=False must not invoke launcher, but got: {cmd!r}"
            )
        if (cmd[:2] == ["opencli", "daemon"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "daemon")):
            return SimpleNamespace(stdout="Daemon: running\n", stderr="", returncode=0)
        if (cmd[:2] == ["opencli", "profile"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "profile")):
            if cmd[-3:] == ["profile", "use", "demo0001"]:
                return SimpleNamespace(stdout="Default Browser Bridge profile: demo0001\n", stderr="", returncode=0)
            # Profile IS connected (demo0001 is the alive binding).
            # Output uses the em-dash separator that _parse_profile_list's
            # regex matches: `name — status`.
            return SimpleNamespace(
                stdout="demo0001 — connected\n",
                stderr="",
                returncode=0,
            )
        if (cmd[:2] == ["opencli", "doctor"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "doctor")):
            return SimpleNamespace(
                stdout="[OK] Daemon: running\n[OK] Extension: connected\n[OK] Connectivity: ok\n",
                stderr="",
                returncode=0,
            )
        if cmd[:2] == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        # resolve_opencli_executable may return a fully qualified path
        # (e.g. ``/home/user/.nvm/versions/node/v24.15.0/bin/opencli``).
        # Match the trailing basename so the test works regardless of
        # how the executable is resolved.
        if os.path.basename(cmd[0]) == "opencli" and len(cmd) >= 2 and cmd[1] == "--version":
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)
    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.application.usecases.browser_session.DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher
    )
    monkeypatch.setattr(
        "backend.application.usecases.browser_session.DEFAULT_OPENCLI_EXTENSION_DIR", ext_dir
    )
    # Stale session-state.json pointing to demo0001 (the alive binding)
    session_json = tmp_path / "browser-session.json"
    session_json.write_text(
        json.dumps({
            "schema_version": 1,
            "profile_dir": str(browser_session.DEFAULT_INFOOS_CHROME_PROFILE_DIR),
            "context_id": "demo0001",
            "alias": "infoos-fetch",
            "started_at": "2026-06-21T00:00:00Z",
            "last_health_at": "2026-06-21T00:00:00Z",
            "last_health_status": "ready",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.application.usecases.browser_session.session_state.JSON_PATH", session_json)
    _patch_profile_running(monkeypatch, lambda: True)

    result = prepare_opencli_browser_session(
        must_rebind=False,
        wait_seconds=5,
        poll_interval_seconds=0.1,
    )

    assert result["status"] == "ready", f"expected ready, got {result!r}"
    # No command in launcher_invocations should be a launcher invocation
    # (i.e. an absolute path ending in opencli-infoos-chrome).
    real_launcher_calls = [
        cmd for cmd in launcher_invocations
        if cmd and str(cmd[0]).endswith("opencli-infoos-chrome")
    ]
    assert not real_launcher_calls, (
        f"must_rebind=False must not invoke the InfoOS Chrome launcher, "
        f"got: {real_launcher_calls!r}"
    )
    assert any(cmd[-3:] == ["profile", "use", "demo0001"] for cmd in launcher_invocations)


def test_must_rebind_false_returns_blocked_when_binding_stale(tmp_path, monkeypatch):
    """must_rebind=False + stale binding = blocked (do not relaunch)."""
    launcher = _make_launcher(tmp_path)
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()

    def fake_run(cmd, **kwargs):
        if (cmd[:2] == ["opencli", "daemon"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "daemon")):
            return SimpleNamespace(stdout="Daemon: running\n", stderr="", returncode=0)
        if (cmd[:2] == ["opencli", "profile"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "profile")):
            # Profile list is EMPTY (stale binding)
            return SimpleNamespace(
                stdout="",
                stderr="",
                returncode=0,
            )
        if (cmd[:2] == ["opencli", "doctor"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "doctor")):
            return SimpleNamespace(
                stdout="[OK] Daemon: running\n[MISSING] Extension: not connected\n",
                stderr="",
                returncode=0,
            )
        if cmd[:2] == ["opencli", "--version"]:
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        if cmd and str(cmd[0]).endswith("opencli-infoos-chrome"):
            raise AssertionError(
                f"must_rebind=False must not invoke launcher even when binding is stale, got: {cmd!r}"
            )
        # resolve_opencli_executable may return a fully qualified path.
        if os.path.basename(cmd[0]) == "opencli" and len(cmd) >= 2 and cmd[1] == "--version":
            return SimpleNamespace(stdout="1.8.0\n", stderr="", returncode=0)
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)
    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.application.usecases.browser_session.DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher
    )
    monkeypatch.setattr(
        "backend.application.usecases.browser_session.DEFAULT_OPENCLI_EXTENSION_DIR", ext_dir
    )
    # Stale session-state.json pointing to a NOW-DEAD context id
    session_json = tmp_path / "browser-session.json"
    session_json.write_text(
        json.dumps({
            "schema_version": 1,
            "profile_dir": str(browser_session.DEFAULT_INFOOS_CHROME_PROFILE_DIR),
            "context_id": "deadbeef",
            "alias": "infoos-fetch",
            "started_at": "2026-06-19T00:00:00Z",
            "last_health_at": "2026-06-19T00:00:00Z",
            "last_health_status": "ready",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.application.usecases.browser_session.session_state.JSON_PATH", session_json)
    _patch_profile_running(monkeypatch, lambda: True)

    result = prepare_opencli_browser_session(
        must_rebind=False,
        wait_seconds=5,
        poll_interval_seconds=0.1,
    )

    assert result["status"] == "blocked", f"expected blocked, got {result!r}"
    # Issues should hint the user to refresh the binding manually
    assert any("binding" in i.lower() or "chrome" in i.lower() for i in result["issues"]), (
        f"expected binding-related issue, got: {result['issues']!r}"
    )


def test_supervised_container_refreshes_stale_pid_without_restart(tmp_path, monkeypatch):
    launcher = _make_launcher(tmp_path)
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    refresh_results = iter([False, True])
    bind_calls: list[dict] = []

    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "supervised-container")
    monkeypatch.setattr(
        "backend.application.usecases.browser_session._opencli_status",
        lambda executable="opencli", **_kwargs: _fake_opencli_healthy(executable),
    )
    _patch_profile_running(monkeypatch, lambda: True)
    monkeypatch.setattr(
        browser_session,
        "_refresh_existing_profile_binding",
        lambda *_args: next(refresh_results),
    )
    monkeypatch.setattr(
        "backend.application.usecases.browser_session.bind_infoos_chrome",
        lambda **kwargs: bind_calls.append(kwargs) or {"ok": True},
    )
    monkeypatch.setattr(
        browser_session,
        "_attempt_browser_repair",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("supervised runtime must not restart Chrome or OpenCLI")
        ),
    )

    result = browser_session._prepare_opencli_browser_session_locked(
        browser_launcher=launcher,
        extension_dir=ext_dir,
        probe_x_login=False,
    )

    assert result["status"] == "ready"
    assert result["repair_steps"] == ["supervised_container_rebind"]
    assert bind_calls == [{"launcher": launcher, "start_url": "https://x.com/home"}]


def test_supervised_binding_rejects_previous_container_pid(monkeypatch):
    state = BrowserSessionState(
        schema_version=2,
        profile_dir=str(browser_session.DEFAULT_INFOOS_CHROME_PROFILE_DIR),
        context_id="ctx-container",
        alias="infoos-fetch",
        chrome_pid=101,
        binding_proof="supervised_container_dedicated_profile",
        started_at="2026-07-28T00:00:00Z",
        last_health_at="2026-07-28T00:00:00Z",
        last_health_status="ready",
    )
    result = {"profile": {}, "issues": []}

    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "supervised-container")
    monkeypatch.setattr(browser_session_profile, "_read_valid_session_state", lambda: state)
    monkeypatch.setattr(
        "backend.application.usecases.browser_session_profile._infoos_chrome_profile_running",
        lambda: True,
    )
    monkeypatch.setattr(
        "backend.application.usecases.browser_session_profile._infoos_chrome_profile_pids",
        lambda: [202],
    )

    assert browser_session_profile._refresh_existing_profile_binding(
        result, "opencli"
    ) is False
    assert result["profile"]["binding_status"] == "stale"
    assert result["profile"]["binding_error"] == "已记录的 Chrome 进程与专用配置不匹配"


# ---------------------------------------------------------------------------
# Real opencli 1.8.0 `profile list` output format
#
# Earlier opencli versions emitted:  "  default — connected v1.0.2"
# 1.8.0 emits:                       "  demo0001 infoos-fetch default — connected v1.0.20"
# browser_session._parse_profile_list must parse this new format.
# Tested through _connected_opencli_profile_names → _parse_profile_list
# ---------------------------------------------------------------------------


def test_connected_opencli_profile_names_parses_opencli_180_real_format(monkeypatch):
    """opencli 1.8.0 emits contextId + alias + 'default' tag + status + version.
    _connected_opencli_profile_names must return the contextId."""
    from backend.application.usecases import browser_session

    def fake_run(cmd, **kwargs):
        if (cmd[:2] == ["opencli", "profile"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "profile")):
            return SimpleNamespace(
                stdout=(
                    "Connected Browser Bridge profiles\n\n"
                    "  demo0001 infoos-fetch default — connected v1.0.20\n\n"
                    "  Update available: v1.8.0 → v1.8.4\n"
                ),
                stderr="",
                returncode=0,
            )
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)
    names, err = browser_session._connected_opencli_profile_names("opencli")
    assert err is None
    assert "demo0001" in names, (
        f"expected demo0001 in connected names, got {names!r}. "
        "browser_session._parse_profile_list does not understand the new "
        "opencli 1.8.0 output format (contextId alias default — status version)."
    )


def test_connected_opencli_profile_names_still_parses_old_format(monkeypatch):
    """Backward compat: old format `name — status` must still parse."""
    from backend.application.usecases import browser_session

    def fake_run(cmd, **kwargs):
        if (cmd[:2] == ["opencli", "profile"] or (len(cmd) >= 2 and os.path.basename(cmd[0]) == "opencli" and cmd[1] == "profile")):
            return SimpleNamespace(
                stdout="  default — connected v1.0.2\n",
                stderr="",
                returncode=0,
            )
        raise AssertionError(f"unexpected subprocess.run: {cmd!r}")

    monkeypatch.setattr("backend.application.usecases.browser_session_profile.subprocess.run", fake_run)
    names, err = browser_session._connected_opencli_profile_names("opencli")
    assert err is None
    assert "default" in names
