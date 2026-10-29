"""Bounded OpenCLI subprocess adapter used by setup workflows."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

from backend.infrastructure.opencli.runtime import resolve_opencli_executable


OPENCLI_PROFILE_ALIAS = "infoos-fetch"


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass
class OpenCLIResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    def __iter__(self):
        yield self.returncode
        yield self.stdout
        yield self.stderr


def run_opencli(args: list[str], *, timeout: float, executable: str = "opencli") -> OpenCLIResult:
    """Run one resolved OpenCLI command without applying business policy."""
    try:
        completed = subprocess.run(
            [resolve_opencli_executable(executable), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return OpenCLIResult(returncode=127, stderr=f"opencli not found: {exc}")
    except subprocess.TimeoutExpired as exc:
        stderr = _coerce_text(exc.stderr) or f"timeout after {timeout}s"
        return OpenCLIResult(
            returncode=124,
            stdout=_coerce_text(exc.stdout),
            stderr=stderr,
        )
    return OpenCLIResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def ensure_daemon(*, timeout: int = 15) -> OpenCLIResult:
    """Start the OpenCLI daemon when an explicit setup action requests it."""
    status = run_opencli(["daemon", "status"], timeout=min(timeout, 5))
    output = f"{status.stdout}\n{status.stderr}".lower()
    if status.returncode == 0 and "daemon: running" in output and "not running" not in output:
        return status
    return run_opencli(["daemon", "restart"], timeout=timeout)


def pin_adapter_lifecycle(
    args: list[str],
    *,
    profile_alias: str = OPENCLI_PROFILE_ALIAS,
    site_session: str = "ephemeral",
    keep_tab: bool = False,
) -> list[str]:
    """Pin an adapter to InfoOS with an explicit site-session lifecycle policy."""
    command = _pin_profile(list(args), profile_alias)
    command = _replace_or_append_option(command, "--site-session", str(site_session))
    command = _replace_or_append_option(command, "--keep-tab", "true" if keep_tab else "false")
    return command


def close_site_session(
    site: str,
    *,
    profile_alias: str = OPENCLI_PROFILE_ALIAS,
    timeout: int = 15,
    executable: str = "opencli",
) -> OpenCLIResult:
    return run_opencli(
        ["--profile", profile_alias, "browser", f"site:{site}", "close"],
        timeout=timeout,
        executable=executable,
    )


def _replace_or_append_option(command: list[str], flag: str, value: str) -> list[str]:
    result = list(command)
    for index, part in enumerate(result):
        if part == flag:
            if index + 1 < len(result):
                result[index + 1] = value
            else:
                result.append(value)
            return result
        if part.startswith(f"{flag}="):
            result[index] = f"{flag}={value}"
            return result
    return [*result, flag, value]


def _pin_profile(command: list[str], profile_alias: str) -> list[str]:
    filtered: list[str] = []
    index = 0
    while index < len(command):
        part = command[index]
        if part == "--profile":
            index += 2
            continue
        if part.startswith("--profile="):
            index += 1
            continue
        filtered.append(part)
        index += 1
    return ["--profile", profile_alias, *filtered]
