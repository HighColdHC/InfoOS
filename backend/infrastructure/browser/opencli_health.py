"""Passive OpenCLI daemon / extension health probe.

Invokes `opencli daemon status` and `opencli profile list` and parses the
human-readable output. Neither command creates Browser Bridge windows.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_offset

import re
import subprocess

from backend.infrastructure.opencli.runtime import resolve_opencli_executable


STATUS_TIMEOUT_SECONDS = 10
PROFILE_LIST_TIMEOUT_SECONDS = 10


def refresh(executable: str = "opencli") -> dict:
    """Refresh OpenCLI health. Returns {
        daemon_running, daemon_version, extension_connected,
        doctor_status, profiles: [{alias, version, status}],
        last_checked_at, hint
    }"""
    resolved_executable = resolve_opencli_executable(executable)
    try:
        status_proc = subprocess.run(
            [resolved_executable, "daemon", "status"],
            capture_output=True, text=True,
            timeout=STATUS_TIMEOUT_SECONDS, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {
            "daemon_running": False,
            "daemon_version": None,
            "extension_connected": False,
            "doctor_status": "failed",
            "profiles": [],
            "last_checked_at": utc_now_offset(),
            "hint": str(exc),
        }

    status = parse_status_output(status_proc.stdout + status_proc.stderr)
    profiles = []
    try:
        profile_proc = subprocess.run(
            [resolved_executable, "profile", "list"],
            capture_output=True, text=True,
            timeout=PROFILE_LIST_TIMEOUT_SECONDS, check=False,
        )
        profiles = parse_profile_list_output(profile_proc.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    extension_connected = status["extension_connected"] or any(
        profile.get("status") == "connected" for profile in profiles
    )
    return {
        **status,
        "extension_connected": extension_connected,
        "doctor_status": (
            "ok"
            if status["daemon_running"] and extension_connected
            else "failed"
        ),
        "profiles": profiles,
        "last_checked_at": utc_now_offset(),
        "hint": status.get("hint"),
    }


def parse_status_output(output: str) -> dict:
    """Parse passive ``daemon status`` or legacy ``doctor`` output."""
    lower = output.lower()
    daemon_running = "[ok] daemon" in lower or "daemon: running" in lower
    extension_connected = (
        ("[ok] connectivity" in lower and "[fail] connectivity" not in lower)
        or "extension: connected" in lower
    )
    return {
        "daemon_running": daemon_running,
        "daemon_version": _extract_daemon_version(output),
        "extension_connected": extension_connected,
        "doctor_status": "ok" if (daemon_running and extension_connected) else "failed",
    }


def parse_doctor_output(output: str) -> dict:
    """Backward-compatible parser name for existing callers and tests."""
    return parse_status_output(output)


def parse_profile_list_output(output: str) -> list[dict]:
    """Parse `opencli profile list` output into structured entries.

    opencli 1.8.0 emits one profile per line. The line format is::

        "  <contextId> [<alias> [default]] — <status> [v<version>]"

    Earlier opencli versions only had a single token before the em-dash::

        "  <name> — <status> v<version>"

    The parser handles both. The first token is always reported as
    ``alias`` in the returned dict (this preserves the previous
    semantic where the first token was the only token — contextId and
    alias were the same value).
    """
    profiles = []
    for line in output.splitlines():
        # Skip noise: header, blank, "Update available", "Run: ..." etc.
        text = line.strip()
        if not text or text.lower().startswith(
            ("connected browser bridge profiles", "update available", "run:", "download:", "open a chrome")
        ):
            continue
        # First token = contextId (new) or alias (old). Then optional
        # alias and an optional "default" tag, then em-dash / dash /
        # colon, then status, then optional version.
        m = re.match(
            r"^\s*\*?\s*(?P<name>\S+)"
            r"(?:\s+(?P<alias>\S+))?"
            r"(?:\s+(?P<default>default))?"
            r"\s*[—\-:]\s*"
            r"(?P<status>\w+)"
            r"(?:\s+v?(?P<version>[\d.]+))?",
            line,
        )
        if m:
            profiles.append({
                "alias": m.group("name"),
                "status": m.group("status").lower(),
                "version": m.group("version"),
            })
    return profiles


def _extract_daemon_version(output: str) -> str | None:
    m = re.search(r"^Version:\s*v?([\d.]+)", output, re.MULTILINE)
    if m:
        return m.group(1)
    m = re.search(r"Daemon:\s*running on port\s+\d+\s+\(v?([\d.]+)\)", output)
    if m:
        return m.group(1)
    return None
