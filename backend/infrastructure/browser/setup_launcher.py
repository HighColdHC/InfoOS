"""Launch the dedicated InfoOS Chrome for the setup wizard.

This is a thin wrapper around the InfoOS launcher script. Unlike
the runtime fetch path, the setup launch is **always** GUI-visible
(per spec section 2.4): the user needs to interact with login pages.
The user's runtime visibility switch (``headless_during_fetch``) is
deliberately ignored here.

The helper must not touch the user's default browser. Install flows can
surface the OpenCLI webstore URL explicitly, but launching the dedicated
InfoOS profile should never start the daily Chrome profile as a side
effect.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from backend.infrastructure.browser.runtime_policy import browser_mutation_block_reason

_START_URL = "https://x.com/home"
_SUPERVISED_CONTAINER_RUNTIME = "supervised-container"
DEFAULT_INFOOS_BROWSER_LAUNCHER = Path.home() / ".local" / "bin" / "opencli-infoos-chrome"

# OpenCLI extension's public webstore page. This is returned for UI/help
# surfaces; it is not opened automatically by the setup launcher.
_OPENCLI_WEBSTORE_URL = (
    "https://chromewebstore.google.com/detail/opencli/"
    "ildkmabpimmkaediidaifkhjpohdnifk?utm_source=ext_app_menu"
)


def launch_infoos_chrome(
    *,
    launcher: Path | None = None,
    start_url: str = _START_URL,
    open_webstore: bool = False,
) -> dict:
    """Run the InfoOS Chrome launcher; return a small status dict.

    The launcher is invoked with no ``--headless`` flag. Setup and
    login always run against a visible Chrome.

    If ``open_webstore`` is True, also fire ``open <webstore-url>``.
    Callers should leave this disabled unless the user explicitly asks
    to open the webstore in their default browser.
    """
    if reason := browser_mutation_block_reason():
        return {"ok": False, "error": reason}
    launcher_path = Path(launcher) if launcher is not None else _default_launcher_path()
    if not launcher_path.exists():
        return {
            "ok": False,
            "error": f"InfoOS Chrome launcher is missing: {launcher_path}",
        }
    # Setup must confirm that the launcher actually reached a visible Chrome
    # process.  A detached Popen only proves that the shell script was spawned;
    # on Linux it used to return success even when Chrome immediately exited
    # because the service had no desktop session.
    try:
        completed = subprocess.run(
            [str(launcher_path), "--launch-only", start_url],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Dedicated Chrome launch timed out"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "error": detail or f"Dedicated Chrome launcher exited with {completed.returncode}",
        }
    if open_webstore:
        try:
            subprocess.Popen(
                ["open", _OPENCLI_WEBSTORE_URL],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError) as exc:
            # Non-fatal: the user can still install the extension manually
            # by visiting the URL.
            return {
                "ok": True,
                "launcher": str(launcher_path),
                "start_url": start_url,
                "warning": f"failed to open webstore page: {exc}",
            }
    return {
        "ok": True,
        "launcher": str(launcher_path),
        "start_url": start_url,
        "webstore_url": _OPENCLI_WEBSTORE_URL,
    }


def bind_infoos_chrome(
    *,
    launcher: Path | None = None,
    start_url: str = _START_URL,
) -> dict:
    """Establish a proven binding for the dedicated InfoOS profile.

    The launcher owns context selection. Native environments prove a context
    delta around a dedicated-Chrome restart; the supervised container proves
    the already-running unique Chrome process and connected context without
    competing with its supervisor. The setup application must not guess
    ownership from ``profile list`` order.
    """
    if reason := browser_mutation_block_reason():
        return {"ok": False, "error": reason}
    launcher_path = Path(launcher) if launcher is not None else _default_launcher_path()
    if not launcher_path.exists():
        return {
            "ok": False,
            "error": f"InfoOS Chrome launcher is missing: {launcher_path}",
        }
    command = [str(launcher_path), start_url]
    runtime = os.environ.get("INFOOS_BROWSER_RUNTIME", "").strip()
    if runtime == _SUPERVISED_CONTAINER_RUNTIME:
        command = [str(launcher_path), "--bind-supervised-container", start_url]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=75,
            check=False,
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Dedicated Chrome binding timed out"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "ok": False,
            "error": detail or f"Dedicated Chrome launcher exited with {completed.returncode}",
        }
    return {
        "ok": True,
        "launcher": str(launcher_path),
        "start_url": start_url,
    }


def restart_infoos_chrome_for_persistence(
    *,
    launcher: Path | None = None,
    start_url: str = _START_URL,
) -> dict:
    """Gracefully restart and rebind an already-proven dedicated Chrome."""
    if reason := browser_mutation_block_reason():
        return {"ok": False, "error": reason}
    launcher_path = Path(launcher) if launcher is not None else _default_launcher_path()
    if not launcher_path.exists():
        return {"ok": False, "error": "Dedicated Chrome launcher is unavailable"}
    try:
        completed = subprocess.run(
            [str(launcher_path), "--persistence-check", start_url],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return {"ok": False, "error": "Dedicated Chrome persistence restart could not start"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Dedicated Chrome persistence restart timed out"}
    if completed.returncode != 0:
        return {"ok": False, "error": "Dedicated Chrome persistence restart failed"}
    return {"ok": True, "launcher": str(launcher_path), "start_url": start_url}


def _default_launcher_path() -> Path:
    raw = os.environ.get("INFOOS_BROWSER_LAUNCHER", "").strip()
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_INFOOS_BROWSER_LAUNCHER
