"""OpenCLI status / diagnostic helpers used by browser-session commands.

Migrated from infoos.cli.opencli_status (PR 2h of the backend restructure).
The ``infoos.cli.opencli_status`` shim was removed in PR 6 along with
the rest of the infoos.cli/ directory.

"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from backend.infrastructure.browser.chrome_runtime import (
    chrome_main_process_matches,
    pid_namespace_matches_current,
)
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.infrastructure.runtime.media_tools import media_tool_status
from backend.application.usecases.source_config import (
    DEFAULT_INFOOS_BROWSER_LAUNCHER,
    DEFAULT_INFOOS_CHROME_BIN,
    DEFAULT_INFOOS_CHROME_PROFILE_DIR,
)


def _opencli_status(executable: str, *, allow_repair: bool = False) -> dict:
    resolved_executable = resolve_opencli_executable(executable)
    status = {
        "executable": resolved_executable,
        "configured_executable": executable,
        "available": False,
        "version": None,
        "daemon_version": None,
        "browser_bridge_connected": False,
        "daemon_running": False,
        "doctor_status": "not_run",
        "doctor_output": None,
        "doctor_hint": None,
        "media_tools": _media_tool_status(),
    }
    try:
        version = subprocess.run(
            [resolved_executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        status["doctor_hint"] = "无法读取 OpenCLI 运行状态"
        return status

    status["available"] = version.returncode == 0
    status["version"] = version.stdout.strip() or None
    if not status["available"]:
        status["doctor_hint"] = "无法读取 OpenCLI 版本信息"
        return status

    try:
        status_command = [resolved_executable, "doctor"] if allow_repair else [resolved_executable, "daemon", "status"]
        doctor = subprocess.run(
            status_command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        status["doctor_status"] = "timeout"
        status["doctor_hint"] = "无法读取 OpenCLI 运行状态"
        return status

    doctor_output = "\n".join(part for part in [doctor.stdout.strip(), doctor.stderr.strip()] if part)
    status["doctor_output"] = doctor_output
    status["doctor_status"] = "ok" if doctor.returncode == 0 else "failed"
    output_lower = doctor_output.lower()
    status["daemon_running"] = "daemon_running" in output_lower or "daemon: running" in output_lower
    status["daemon_version"] = _extract_opencli_daemon_version(doctor_output)
    status["browser_bridge_connected"] = (
        ("[ok] connectivity" in output_lower and "[fail] connectivity" not in output_lower)
        or "extension: connected" in output_lower
    )
    if not status["browser_bridge_connected"]:
        status["doctor_hint"] = "Browser Bridge 扩展未连接"
    return status


def _media_tool_status() -> dict[str, dict[str, str | None]]:
    return media_tool_status()


def _extract_opencli_daemon_version(output: str) -> str | None:
    version_match = re.search(r"Version:\s*v?([0-9][^\s)]*)", output)
    if version_match:
        return version_match.group(1)
    port_match = re.search(r"port\s+\d+\s+\(v?([0-9][^\s)]*)\)", output)
    if port_match:
        return port_match.group(1)
    return None


def _browser_session_diagnostic(
    required: bool,
    *,
    browser_launcher: str | Path | None = None,
    profile_dir: str | Path | None = None,
) -> dict:
    launcher = Path(browser_launcher) if browser_launcher is not None else DEFAULT_INFOOS_BROWSER_LAUNCHER
    profile = Path(profile_dir) if profile_dir is not None else DEFAULT_INFOOS_CHROME_PROFILE_DIR
    return {
        "required": required,
        "launcher": str(launcher),
        "launcher_exists": launcher.exists(),
        "profile_dir": str(profile),
        "isolated_profile": True,
        "next_action": f"python -m backend.application.usecases.browser_session prepare-browser-session --browser-launcher {launcher}",
    }


def _infoos_chrome_profile_pids(profile_dir: str | Path | None = None) -> list[int]:
    profile = Path(profile_dir) if profile_dir is not None else DEFAULT_INFOOS_CHROME_PROFILE_DIR
    process_pattern = f"--user-data-dir={profile}"
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-f", "--", process_pattern],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for pid_text in result.stdout.splitlines():
        pid_text = pid_text.strip()
        if not pid_text:
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        try:
            command = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        command_line = command.stdout or ""
        if (
            process_pattern in command_line
            and _is_chrome_main_process(command_line)
            and "Helper" not in command_line
            and _pid_owned_by_current_user(pid)
            and pid_namespace_matches_current(pid)
        ):
            try:
                pids.append(pid)
            except ValueError:
                continue
    return pids


def _is_chrome_main_process(command_line: str) -> bool:
    return chrome_main_process_matches(command_line, DEFAULT_INFOOS_CHROME_BIN)


def _pid_owned_by_current_user(pid: int) -> bool:
    proc_dir = Path(f"/proc/{pid}")
    if not proc_dir.exists():
        return True
    try:
        return proc_dir.stat().st_uid == os.geteuid()
    except OSError:
        return False


def _infoos_chrome_profile_running(profile_dir: str | Path | None = None) -> bool:
    return bool(_infoos_chrome_profile_pids(profile_dir))


def _infoos_chrome_profile_headless(profile_dir: str | Path | None = None) -> bool:
    """Return True when the dedicated InfoOS Chrome profile is running headless."""
    pids = _infoos_chrome_profile_pids() if profile_dir is None else _infoos_chrome_profile_pids(profile_dir)
    for pid in pids:
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
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    """Module entrypoint: run ``_opencli_status`` and print JSON result.

    Usage:
        python -m backend.application.usecases.opencli_status
        python -m backend.application.usecases.opencli_status --executable opencli --allow-repair
    """
    import argparse
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", default="opencli")
    parser.add_argument("--allow-repair", action="store_true", help="Run 'opencli doctor' instead of 'daemon status'")
    args = parser.parse_args(argv)
    result = _opencli_status(args.executable, allow_repair=args.allow_repair)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
