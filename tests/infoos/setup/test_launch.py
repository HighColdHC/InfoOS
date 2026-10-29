"""Setup launch: invokes the InfoOS Chrome launcher with headless always False."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.browser import setup_launcher as launch  # noqa: E402


@pytest.mark.parametrize(
    ("action", "expected_error"),
    [
        (
            lambda: launch.launch_infoos_chrome(start_url="https://x.com/login"),
            "Container Browser Runtime is required",
        ),
        (
            lambda: launch.bind_infoos_chrome(start_url="https://x.com/login"),
            "Container Browser Runtime is required",
        ),
        (
            lambda: launch.restart_infoos_chrome_for_persistence(
                start_url="https://x.com/login"
            ),
            "Container Browser Runtime is required",
        ),
    ],
)
def test_deployed_linux_browser_mutations_fail_closed_outside_container(
    monkeypatch,
    tmp_path,
    action,
    expected_error,
):
    """The legacy systemd deployment must never launch the host Chrome.

    ``/var/lib/infoos/state`` is the deployed Linux state contract. Browser
    mutations at that path are only valid inside the supervised Docker
    Appliance; the native systemd Web must fail before spawning a launcher.
    """
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("INFOOS_STATE_HOME", "/var/lib/infoos/state")
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "native")
    monkeypatch.setenv("INFOOS_BROWSER_LAUNCHER", str(launcher_path))

    def unexpected_run(*_args, **_kwargs):
        raise AssertionError("host launcher must not run")

    monkeypatch.setattr(launch.subprocess, "run", unexpected_run)

    result = action()

    assert result["ok"] is False
    assert expected_error in result["error"]


def test_launch_infoos_chrome_invokes_launcher(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(launch.subprocess, "run", fake_run)

    result = launch.launch_infoos_chrome()
    assert result["ok"] is True
    assert run_calls[0][0] == str(launcher_path)
    # Repeated opens let Chrome focus/reuse the existing dedicated profile.
    assert "--restart" not in run_calls[0]
    assert "--launch-only" in run_calls[0]
    # The URL arg is appended positionally
    assert any("https://x.com/home" in part for part in run_calls[0])
    assert len(run_calls) == 1


def test_bind_infoos_chrome_invokes_full_proving_launcher(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    run_calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append((list(cmd), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "native")

    result = launch.bind_infoos_chrome()

    assert result["ok"] is True
    assert run_calls[0][0] == [str(launcher_path), "https://x.com/home"]
    assert run_calls[0][1]["timeout"] == 75


def test_bind_infoos_chrome_uses_supervised_container_binding(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    monkeypatch.setenv("INFOOS_BROWSER_RUNTIME", "supervised-container")

    result = launch.bind_infoos_chrome()

    assert result["ok"] is True
    assert run_calls == [[
        str(launcher_path),
        "--bind-supervised-container",
        "https://x.com/home",
    ]]


def test_bind_infoos_chrome_propagates_persistence_failure(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(
        launch.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Could not persist browser-session.json",
        ),
    )

    result = launch.bind_infoos_chrome()

    assert result == {"ok": False, "error": "Could not persist browser-session.json"}


def test_persistence_restart_invokes_strict_launcher_mode(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(
        launch.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append((list(cmd), kwargs)) or SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )

    result = launch.restart_infoos_chrome_for_persistence()

    assert result["ok"] is True
    assert calls[0][0] == [
        str(launcher_path),
        "--persistence-check",
        "https://x.com/home",
    ]
    assert calls[0][1]["timeout"] == 120


def test_persistence_restart_does_not_expose_launcher_error(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(
        launch.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=26, stdout="", stderr="secret-shaped launcher detail"
        ),
    )

    assert launch.restart_infoos_chrome_for_persistence() == {
        "ok": False,
        "error": "Dedicated Chrome persistence restart failed",
    }


def test_launch_infoos_chrome_can_optionally_open_webstore(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    run_calls: list[list[str]] = []
    popen_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_popen(cmd, **kwargs):
        popen_calls.append(list(cmd))
        return None

    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)
    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    monkeypatch.setattr(launch.subprocess, "Popen", fake_popen)

    result = launch.launch_infoos_chrome(open_webstore=True)

    assert result["ok"] is True
    assert run_calls[0][0] == str(launcher_path)
    assert popen_calls, "expected Popen to be called for the webstore URL"
    assert popen_calls[0][0] == "open"
    assert any("chromewebstore.google.com" in part for part in popen_calls[0])


def test_launch_infoos_chrome_returns_error_on_missing_launcher(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", tmp_path / "missing")
    result = launch.launch_infoos_chrome()
    assert result["ok"] is False
    assert "missing" in result["error"].lower() or "launcher" in result["error"].lower()


def test_launch_infoos_chrome_prefers_env_launcher(monkeypatch, tmp_path):
    env_launcher = tmp_path / "env-opencli-infoos-chrome"
    env_launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", tmp_path / "missing")
    monkeypatch.setenv("INFOOS_BROWSER_LAUNCHER", str(env_launcher))
    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)

    result = launch.launch_infoos_chrome()

    assert result["ok"] is True
    assert run_calls[0][0] == str(env_launcher)


def test_launch_infoos_chrome_returns_error_when_launcher_spawn_fails(monkeypatch, tmp_path):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)

    def boom(cmd, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(launch.subprocess, "run", boom)
    result = launch.launch_infoos_chrome()
    assert result["ok"] is False
    assert "boom" in result["error"]


def test_launch_infoos_chrome_does_not_report_success_when_launcher_exits_without_chrome(
    monkeypatch,
    tmp_path,
):
    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text(
        "#!/usr/bin/env bash\necho 'visible Chrome requires a desktop session' >&2\nexit 23\n",
        encoding="utf-8",
    )
    launcher_path.chmod(0o755)
    monkeypatch.setenv("INFOOS_BROWSER_LAUNCHER", str(launcher_path))

    result = launch.launch_infoos_chrome()

    assert result == {
        "ok": False,
        "error": "visible Chrome requires a desktop session",
    }


def test_launch_infoos_chrome_ignores_user_visibility_config(monkeypatch, tmp_path):
    """The setup launch path is always GUI, regardless of the runtime visibility switch."""
    from backend.application.setup import visibility
    # Pretend user has flipped the switch to hidden.
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  headless_during_fetch: true\n", encoding="utf-8")
    monkeypatch.setenv("INFOOS_CONFIG_PATH", str(config))
    assert visibility.read_headless_during_fetch() is True

    launcher_path = tmp_path / "opencli-infoos-chrome"
    launcher_path.write_text("#!/bin/zsh\n", encoding="utf-8")
    monkeypatch.setattr(launch, "DEFAULT_INFOOS_BROWSER_LAUNCHER", launcher_path)

    run_calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        run_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    launch.launch_infoos_chrome()
    # No --headless flag, even though visibility says True
    assert "--headless" not in run_calls[0]
