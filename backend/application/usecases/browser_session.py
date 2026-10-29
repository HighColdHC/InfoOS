"""Browser-session lifecycle: prepare, launch, poll, repair, probe X readiness.

Migrated from infoos.cli.browser_session (PR 2i of the backend restructure).
Split into three files:
  - browser_session.py        (this file): public prepare/status entry points
  - browser_session_profile.py: profile binding helpers
  - browser_session_x.py      : X readiness probe + browser-process helpers

The ``infoos.cli.browser_session`` shim was removed in PR 6 along with
the rest of the infoos.cli/ directory.

"""
from __future__ import annotations

import os
from pathlib import Path

from backend.application.usecases.browser_session_profile import (
    _connected_opencli_profile_names,
    _ensure_profile_binding,
    _profile_payload,
    _refresh_existing_profile_binding,
)
from backend.application.usecases.browser_session_x import (
    _apply_x_readiness_probe,
    _kill_infoos_chrome,
    _kill_infoos_headless_chrome,
    _launch_browser,
    _mark_ready_without_login_probe,
    _poll_browser_session,
    _restart_opencli_daemon,
    _wait_for_page_settle,
    _x_login_unknown,
)
from backend.infrastructure.browser import session_state
from backend.infrastructure.browser.runtime_policy import browser_mutation_block_reason
from backend.infrastructure.browser.session_state import BrowserSessionState
from backend.infrastructure.browser.setup_launcher import bind_infoos_chrome
from backend.infrastructure.opencli.runtime import opencli_browser_lock, resolve_opencli_executable
from backend.infrastructure.opencli.tab_lease import opencli_profile_write_lock
from backend.application.usecases.opencli_status import (
    _infoos_chrome_profile_headless,
    _infoos_chrome_profile_running,
    _opencli_status,
)
from backend.application.usecases.source_config import (
    DEFAULT_INFOOS_BROWSER_LAUNCHER,
    DEFAULT_INFOOS_CHROME_PROFILE_DIR as DEFAULT_INFOOS_CHROME_PROFILE_DIR,
    DEFAULT_OPENCLI_EXTENSION_DIR,
)
from backend.application.setup.visibility import read_headless_during_fetch


def prepare_opencli_browser_session(
    *,
    executable: str = "opencli",
    browser_app: str = "Google Chrome",
    browser_launcher: str | Path | None = None,
    extension_dir: str | Path | None = None,
    open_url: str = "https://x.com/home",
    wait_seconds: int = 20,
    poll_interval_seconds: float = 2.0,
    probe_x_login: bool = True,
    must_rebind: bool = True,
) -> dict:
    if reason := browser_mutation_block_reason():
        return _blocked_browser_runtime_result(
            executable=executable,
            browser_app=browser_app,
            browser_launcher=browser_launcher,
            extension_dir=extension_dir,
            open_url=open_url,
            reason=reason,
        )
    with opencli_profile_write_lock():
        with opencli_browser_lock():
            return _prepare_opencli_browser_session_locked(
                executable=executable,
                browser_app=browser_app,
                browser_launcher=browser_launcher,
                extension_dir=extension_dir,
                open_url=open_url,
                wait_seconds=wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                probe_x_login=probe_x_login,
                must_rebind=must_rebind,
            )


def _prepare_opencli_browser_session_locked(
    *,
    executable: str = "opencli",
    browser_app: str = "Google Chrome",
    browser_launcher: str | Path | None = None,
    extension_dir: str | Path | None = None,
    open_url: str = "https://x.com/home",
    wait_seconds: int = 20,
    poll_interval_seconds: float = 2.0,
    probe_x_login: bool = True,
    must_rebind: bool = True,
) -> dict:
    launcher = Path(browser_launcher) if browser_launcher is not None else _default_infoos_browser_launcher()
    ext_dir = Path(extension_dir) if extension_dir is not None else DEFAULT_OPENCLI_EXTENSION_DIR
    if not launcher.exists():
        return {
            "executable": executable,
            "browser_app": browser_app,
            "browser_launcher": str(launcher),
            "browser_launch_method": "missing_launcher",
            "extension_dir": str(ext_dir),
            "extension_dir_exists": ext_dir.is_dir(),
            "open_url": open_url,
            "browser_opened": False,
            "browser_headless": True,
            "browser_bridge_connected": False,
            "repair_steps": [],
            "opencli": _opencli_status(executable),
            "profile": _profile_payload(running=False, binding_status="missing_launcher"),
            "x_login": _x_login_unknown(),
            "issues": [f"Dedicated InfoOS browser launcher is missing: {launcher}"],
            "status": "blocked",
        }
    profile_running = _infoos_chrome_profile_running()
    result = {
        "executable": executable,
        "browser_app": browser_app,
        "browser_launcher": str(launcher) if launcher else None,
        "browser_launch_method": None,
        "extension_dir": str(ext_dir),
        "extension_dir_exists": ext_dir.is_dir(),
        "open_url": open_url,
        "browser_opened": False,
        "browser_headless": True,
        "browser_bridge_connected": False,
        "repair_steps": [],
        "opencli": None,
        "profile": _profile_payload(running=profile_running),
        "x_login": _x_login_unknown(),
        "issues": [],
        "status": "blocked",
    }

    if not ext_dir.is_dir():
        result["issues"].append(f"Extension directory not found: {ext_dir}")

    opencli = _opencli_status(executable, allow_repair=False)
    result["opencli"] = opencli
    result["browser_bridge_connected"] = opencli["browser_bridge_connected"]
    if not opencli["available"]:
        result["issues"].append(f"OpenCLI executable not available: {executable}")
        return result
    binding_ready = _refresh_existing_profile_binding(result, executable)
    if binding_ready:
        # ``daemon status`` is intentionally passive and older OpenCLI builds
        # may omit their extension line. A connected dedicated profile is
        # stronger evidence that the Browser Bridge is available.
        opencli["browser_bridge_connected"] = True
        opencli["doctor_hint"] = None
        result["browser_bridge_connected"] = True
    # Passive-check short-circuit: the setup wizard verify path passes
    # must_rebind=False so we don't relaunch Chrome (which would interrupt
    # the user who is actively using it). If the binding is already alive,
    # we proceed normally below. If it's stale, we report blocked without
    # touching the launcher — the user must manually reload InfoOS Chrome.
    if not must_rebind:
        if opencli["browser_bridge_connected"] and result["profile"]["running"] and binding_ready:
            result["status"] = "ready"
            result["issues"] = []
            return result
        # Binding lost or Chrome not running. Report blocked with a hint;
        # do not invoke the launcher.
        result.setdefault("issues", []).append(
            "OpenCLI profile is not bound. Open InfoOS Chrome and let the "
            "OpenCLI extension reconnect to the daemon before retrying."
        )
        result["status"] = "blocked"
        return result
    if (
        os.environ.get("INFOOS_BROWSER_RUNTIME", "").strip()
        == "supervised-container"
        and not binding_ready
    ):
        # A recreated container can retain the previous container's Chrome PID
        # in its persisted binding. Refresh that proof against the one
        # supervised Chrome and connected context; never compete with the
        # Chrome/OpenCLI supervisors by running the generic restart escalation.
        result["repair_steps"].append("supervised_container_rebind")
        if opencli["browser_bridge_connected"] and result["profile"]["running"]:
            rebound = bind_infoos_chrome(
                launcher=launcher,
                start_url=open_url,
            )
            if rebound.get("ok") and _refresh_existing_profile_binding(
                result, executable
            ):
                _mark_ready_without_login_probe(result)
                return result
            _append_issue(
                result,
                str(rebound.get("error") or "Supervised-container binding refresh failed"),
            )
        else:
            _append_issue(
                result,
                "Supervised Chrome or its OpenCLI extension is not ready",
            )
        result["status"] = "blocked"
        return result
    desired_headless = read_headless_during_fetch()
    actual_headless = _infoos_chrome_profile_headless() if result["profile"]["running"] else desired_headless
    result["browser_headless"] = actual_headless
    # A visible, user-opened dedicated profile is the safest authenticated
    # session. Do not replace it with a headless copy just because fetch-time
    # headless mode is enabled; that can create an orphaned background Chrome
    # while the visible login window is still around.
    profile_visibility_mismatch = (
        result["profile"]["running"]
        and actual_headless
        and not desired_headless
    )
    if (
        opencli["browser_bridge_connected"]
        and result["profile"]["running"]
        and binding_ready
        and not profile_visibility_mismatch
    ):
        result["profile"]["running"] = True
        if not probe_x_login:
            _mark_ready_without_login_probe(result)
            return result
        if _apply_x_readiness_probe(result, executable, timeout_seconds=30, use_lock=False):
            return result
        return result
    if profile_visibility_mismatch:
        if _attempt_browser_repair(
            result=result,
            level=1,
            pre_action=_kill_infoos_chrome,
            launcher=launcher,
            open_url=open_url,
            executable=executable,
            launch_mode="restart",
            wait_seconds=wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            probe_x_login=probe_x_login,
        ):
            return result

    # Repair escalation: try non-destructive first, then daemon restart, then destructive.
    # All three steps share the same launch → poll → settle → probe pattern; the only
    # difference is the pre-action and the launch mode.
    if not _attempt_browser_repair(
        result=result,
        level=1,
        pre_action=None,
        launcher=launcher,
        open_url=open_url,
        executable=executable,
        launch_mode="reopen",
        wait_seconds=wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        probe_x_login=probe_x_login,
    ):
        if not _attempt_browser_repair(
            result=result,
            level=2,
            pre_action=lambda: _restart_opencli_daemon(executable, result),
            launcher=launcher,
            open_url=open_url,
            executable=executable,
            launch_mode="reopen",
            wait_seconds=wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
            probe_x_login=probe_x_login,
        ):
            _attempt_browser_repair(
                result=result,
                level=3,
                pre_action=_kill_infoos_chrome,
                launcher=launcher,
                open_url=open_url,
                executable=executable,
                launch_mode="restart",
                wait_seconds=wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                probe_x_login=probe_x_login,
            )

    if not result["profile"]["running"]:
        result["issues"].append("Dedicated InfoOS Chrome profile is not running")
    if not result["profile"]["bound"]:
        _append_issue(
            result,
            result["profile"].get("binding_error")
            or "Dedicated InfoOS OpenCLI profile is not bound",
        )
    if not result["opencli"]["browser_bridge_connected"]:
        result["issues"].append(
            result["opencli"]["doctor_hint"]
            or "Browser Bridge extension is not connected"
        )
    return result


def _attempt_browser_repair(
    *,
    result: dict,
    level: int,
    pre_action,
    launcher,
    open_url: str,
    executable: str,
    launch_mode: str,
    wait_seconds: int,
    poll_interval_seconds: float,
    probe_x_login: bool,
) -> bool:
    """Run one Browser Bridge repair step.

    ``launch_mode`` is "reopen" for steps 1-2 (reuse existing Chrome) or
    "restart" for step 3 (force-kill Chrome profile and re-launch). Returns
    True if the bridge reconnected and the X readiness probe completed.
    """
    result["repair_steps"].append(f"step{level}_{launch_mode}")
    if pre_action is not None:
        pre_action()
    previous_profiles, _ = _connected_opencli_profile_names(executable)
    if launch_mode == "restart":
        _launch_browser(launcher, open_url, restart=True, result=result)
    else:
        _launch_browser(launcher, open_url, reopen=True, result=result)

    opencli = _poll_browser_session(
        executable, result,
        wait_seconds=wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    binding_ready = _ensure_profile_binding(
        result,
        executable,
        previous_connected_profiles=previous_profiles,
    )
    if not (opencli["browser_bridge_connected"] and result["profile"]["running"] and binding_ready):
        return False
    _wait_for_page_settle()
    if not probe_x_login:
        _mark_ready_without_login_probe(result)
        return True
    _apply_x_readiness_probe(result, executable, timeout_seconds=30, use_lock=False)
    return True


def _append_issue(result: dict, issue: str) -> None:
    if issue and issue not in result["issues"]:
        result["issues"].append(issue)


def _default_infoos_browser_launcher() -> Path:
    raw = os.environ.get("INFOOS_BROWSER_LAUNCHER", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_INFOOS_BROWSER_LAUNCHER


def cleanup_headless_browser_session() -> None:
    """Close only the headless InfoOS Chrome profile, preserving visible login windows."""
    if browser_mutation_block_reason():
        return
    with opencli_profile_write_lock():
        with opencli_browser_lock():
            _kill_infoos_headless_chrome()


def _blocked_browser_runtime_result(
    *,
    executable: str,
    browser_app: str,
    browser_launcher: str | Path | None,
    extension_dir: str | Path | None,
    open_url: str,
    reason: str,
) -> dict:
    launcher = (
        Path(browser_launcher)
        if browser_launcher is not None
        else _default_infoos_browser_launcher()
    )
    ext_dir = (
        Path(extension_dir)
        if extension_dir is not None
        else DEFAULT_OPENCLI_EXTENSION_DIR
    )
    return {
        "executable": executable,
        "browser_app": browser_app,
        "browser_launcher": str(launcher),
        "browser_launch_method": "blocked_runtime",
        "extension_dir": str(ext_dir),
        "extension_dir_exists": ext_dir.is_dir(),
        "open_url": open_url,
        "browser_opened": False,
        "browser_headless": True,
        "browser_bridge_connected": False,
        "repair_steps": [],
        "opencli": _opencli_not_configured_status(executable),
        "profile": _profile_payload(
            running=False,
            binding_status="blocked_runtime",
        ),
        "x_login": _x_login_unknown(),
        "issues": [reason],
        "status": "blocked",
    }


def browser_session_status(*, executable: str = "opencli", probe_x_login: bool = False) -> dict:
    """Return passive browser session status without starting OpenCLI or Chrome."""
    executable = resolve_opencli_executable(executable)
    state = session_state.read()
    profile_running = _infoos_chrome_profile_running()
    if not session_state.is_alive(state):
        return {
            "status": "blocked",
            "opencli": _opencli_not_configured_status(executable),
            "daemon": {"running": False},
            "extension": {"connected": False},
            "profile": _profile_payload(
                running=profile_running,
                binding_status="not_configured",
            ),
            "x_login": _x_login_unknown(),
        }
    return {
        "status": "ready" if profile_running else "blocked",
        "opencli": _opencli_not_checked_status(executable),
        "daemon": {"running": False},
        "extension": {"connected": False},
        "profile": _profile_payload(
            running=profile_running,
            state=state if isinstance(state, BrowserSessionState) else None,
            binding_status="ready" if profile_running else "not_running",
        ),
        "x_login": _x_login_unknown(),
    }


def _opencli_not_configured_status(executable: str) -> dict:
    return {
        "executable": executable,
        "configured_executable": executable,
        "available": False,
        "version": None,
        "daemon_version": None,
        "browser_bridge_connected": False,
        "daemon_running": False,
        "doctor_status": "not_configured",
        "doctor_output": None,
        "doctor_hint": "Dedicated browser session is not configured",
    }


def _opencli_not_checked_status(executable: str) -> dict:
    return {
        "executable": executable,
        "configured_executable": executable,
        "available": False,
        "version": None,
        "daemon_version": None,
        "browser_bridge_connected": False,
        "daemon_running": False,
        "doctor_status": "not_checked",
        "doctor_output": None,
        "doctor_hint": (
            "Passive browser status does not start OpenCLI; browser automation "
            "runs only during explicit setup or browser-backed fetch tasks"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    """Module entrypoint: run ``prepare_opencli_browser_session`` and print JSON result.

    Usage:
        python -m backend.application.usecases.browser_session
        python -m backend.application.usecases.browser_session --executable opencli --url https://x.com/home
    """
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", default="opencli")
    parser.add_argument("--browser-app", default="Google Chrome")
    parser.add_argument("--browser-launcher", type=Path, default=None)
    parser.add_argument("--extension-dir", type=Path, default=None)
    parser.add_argument("--url", default="https://x.com/home")
    parser.add_argument("--wait-seconds", type=int, default=20)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--no-probe-x-login", action="store_true", help="Skip X login probe")
    args = parser.parse_args(argv)
    result = prepare_opencli_browser_session(
        executable=args.executable,
        browser_app=args.browser_app,
        browser_launcher=args.browser_launcher,
        extension_dir=args.extension_dir,
        open_url=args.url,
        wait_seconds=args.wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        probe_x_login=not args.no_probe_x_login,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
