"""Browser-session X-readiness + browser-process helpers.

Split from infoos.cli.browser_session (PR 2i of the backend restructure;
the infoos.cli/ shim was removed in PR 6).

Owns:
  - X readiness probe: ``_x_login_unknown``, ``_wait_for_page_settle``,
    ``_parse_x_profile_payload``, ``_probe_x_readiness``,
    ``_apply_x_readiness_probe``.
  - Browser-process management: ``_launch_browser``, ``_poll_browser_session``,
    ``_restart_opencli_daemon``, ``_kill_infoos_chrome``.
  - Misc helpers used across the split: ``_mark_ready_without_login_probe``.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from contextlib import nullcontext
from pathlib import Path

from backend.infrastructure.opencli.runtime import opencli_browser_lock, resolve_opencli_executable
from backend.application.setup.visibility import read_headless_during_fetch
from backend.application.usecases.opencli_status import (
    _infoos_chrome_profile_pids,
    _infoos_chrome_profile_running,
    _opencli_status,
)

def _mark_ready_without_login_probe(result: dict) -> None:
    result["status"] = "ready"
    result["issues"] = []


def _x_login_unknown() -> dict:
    return {
        "checked": False,
        "status": "unknown",
        "error": None,
    }


def _wait_for_page_settle() -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        time.sleep(1)


def _parse_x_profile_payload(stdout: str, stderr: str) -> tuple[dict | None, str | None]:
    """Best-effort parse of `twitter profile` output.

    Returns ``(payload, format)``. ``payload`` is None when nothing
    recognizable was found. ``format`` is "json", "yaml", or None.

    OpenCLI 1.8.0 emits JSON on success but a YAML error envelope on
    failure. Both forms must be accepted so the readiness probe works
    whether the user is signed in or not. The success path can be a
    JSON *object* (older versions) or a JSON *array* with one identity
    object (1.8.0's ``twitter profile``); both shapes are normalized
    to the inner dict.
    """
    text = (stdout or "").strip()
    if text.startswith("{") or text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded, "json"
            if isinstance(loaded, list) and loaded and isinstance(loaded[0], dict):
                return loaded[0], "json"
        except json.JSONDecodeError:
            pass
    yaml_blob = "\n".join([stdout or "", stderr or ""])
    identity_keys = ("screen_name", "username", "id", "user_id", "rest_id")
    found: dict[str, str] = {}
    for line in yaml_blob.splitlines():
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip().strip("'\"")
        if key in identity_keys and key not in found:
            found[key] = value
    if found:
        return found, "yaml"
    return None, None


def _probe_x_readiness(executable: str, *, timeout_seconds: int = 30, use_lock: bool = True) -> dict:
    x_login = _x_login_unknown()
    x_login["checked"] = True
    resolved_executable = resolve_opencli_executable(executable)
    try:
        lock_context = opencli_browser_lock() if use_lock else nullcontext()
        with lock_context:
            # `twitter profile` without a username returns the logged-in
            # user's identity. An unauthenticated probe returns rc=69 with
            # a YAML error envelope on stderr. The old `twitter tweets
            # elonmusk` probe could not distinguish logged-in from
            # logged-out because elonmusk's tweets are public.
            probe = subprocess.run(
                [resolved_executable, "twitter", "profile", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
    except FileNotFoundError as exc:
        x_login["error"] = str(exc)
        return x_login
    except subprocess.TimeoutExpired as exc:
        x_login["status"] = "timeout"
        x_login["error"] = str(exc)
        return x_login

    combined = f"{probe.stdout}\n{probe.stderr}".lower()
    payload, _fmt = _parse_x_profile_payload(probe.stdout, probe.stderr)

    # ok=false envelope from OpenCLI 1.8.0 (some commands report this
    # at rc=0). Treat as logged_out regardless of rc.
    if isinstance(payload, dict) and payload.get("ok") is False:
        err = payload.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        x_login["status"] = "logged_out"
        x_login["error"] = (msg or "").strip() or probe.stderr.strip() or probe.stdout.strip()
        return x_login

    identity_keys = ("screen_name", "username", "id", "user_id", "rest_id")
    if isinstance(payload, dict) and any(payload.get(k) for k in identity_keys):
        x_login["status"] = "logged_in"
        return x_login

    if probe.returncode == 0:
        # No body we recognize but the process succeeded — be conservative.
        x_login["status"] = "failed"
        x_login["error"] = "X profile probe returned no user identity"
        return x_login

    if any(marker in combined for marker in ("auth_required", "not logged in", "no ct0 cookie",
                                            "browser profile", "is not connected", "browser_connect")):
        x_login["status"] = "logged_out"
    else:
        x_login["status"] = "failed"
    x_login["error"] = probe.stderr.strip() or probe.stdout.strip()
    return x_login


def _apply_x_readiness_probe(result: dict, executable: str, *, timeout_seconds: int, use_lock: bool = True) -> bool:
    x_login = _probe_x_readiness(executable, timeout_seconds=timeout_seconds, use_lock=use_lock)
    result["x_login"] = x_login
    if x_login["status"] == "logged_in":
        result["status"] = "ready"
        result["issues"] = []
        return True
    detail = x_login.get("error") or x_login["status"]
    result["issues"].append(f"X readiness probe failed: {detail}")
    result["status"] = "blocked"
    return False


def _launch_browser(
    launcher: Path | None,
    open_url: str,
    *,
    browser_app: str = "Google Chrome",
    reopen: bool = False,
    restart: bool = False,
    result: dict,
) -> None:
    if launcher and launcher.exists():
        result["browser_launch_method"] = "launcher"
        headless = read_headless_during_fetch()
        result["browser_headless"] = headless
        cmd = [str(launcher)]
        if restart:
            cmd.append("--restart")
        elif reopen:
            pass  # Don't pass --restart on reopen
        cmd.append(open_url)
        if headless:
            cmd.append("--headless")
    else:
        del browser_app
        result["browser_launch_method"] = "missing_launcher"
        result["issues"].append("Dedicated InfoOS browser launcher is missing")
        result["browser_opened"] = False
        return
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        result["browser_opened"] = True
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        result["issues"].append(f"Could not launch browser session: {exc}")
        result["browser_opened"] = False


def _poll_browser_session(
    executable: str,
    result: dict,
    *,
    wait_seconds: int = 20,
    poll_interval_seconds: float = 2.0,
) -> dict:
    deadline = time.monotonic() + max(0, wait_seconds)
    while True:
        opencli = _opencli_status(executable, allow_repair=False)
        result["opencli"] = opencli
        result["browser_bridge_connected"] = opencli["browser_bridge_connected"]
        result["profile"]["running"] = _infoos_chrome_profile_running()
        if opencli["browser_bridge_connected"] and result["profile"]["running"]:
            return opencli
        if time.monotonic() >= deadline:
            return opencli
        time.sleep(max(0.1, poll_interval_seconds))


def _restart_opencli_daemon(executable: str, result: dict) -> None:
    resolved_executable = resolve_opencli_executable(executable)
    try:
        subprocess.run(
            [resolved_executable, "daemon", "stop"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except subprocess.TimeoutExpired:
        pass
    try:
        os.makedirs(Path.home() / ".opencli", exist_ok=True)
        restart = subprocess.run(
            [resolved_executable, "daemon", "restart"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if restart.returncode == 0:
            time.sleep(2)
            return
        subprocess.Popen([resolved_executable, "daemon", "--start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError):
        result["issues"].append("Could not restart OpenCLI daemon")
    except subprocess.TimeoutExpired:
        subprocess.Popen([resolved_executable, "daemon", "--start"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)


def _kill_infoos_chrome() -> None:
    pids = _infoos_chrome_profile_pids()
    _terminate_chrome_pids(pids)
    time.sleep(3)


def _kill_infoos_headless_chrome() -> None:
    """Terminate only headless Chrome main processes for the InfoOS profile."""
    _terminate_chrome_pids(_infoos_headless_chrome_profile_pids())


def _infoos_headless_chrome_profile_pids() -> list[int]:
    pids: list[int] = []
    for pid in _infoos_chrome_profile_pids():
        try:
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if "--headless" in (result.stdout or ""):
            pids.append(pid)
    return pids


def _terminate_chrome_pids(pids: list[int]) -> None:
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not _target_pids_alive(pids):
            return
        time.sleep(1)
    for pid in _infoos_chrome_profile_pids():
        if pid not in pids:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(3)


def _target_pids_alive(pids: list[int]) -> bool:
    alive = set(_infoos_chrome_profile_pids())
    return any(pid in alive for pid in pids)
