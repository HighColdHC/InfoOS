from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path


MACOS_STABLE_CHROME = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
LINUX_STABLE_CHROME_CANDIDATES = (
    Path("/usr/bin/google-chrome-stable"),
    Path("/usr/bin/google-chrome"),
    Path("/opt/google/chrome/google-chrome"),
)


def chrome_binary_candidates(*, system: str | None = None) -> list[Path]:
    """Return stable-Chrome candidates in deployment priority order."""
    candidates: list[Path] = []
    configured = os.environ.get("INFOOS_CHROME_BIN", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    current_system = system or platform.system()
    if current_system == "Darwin":
        candidates.append(MACOS_STABLE_CHROME)
    elif current_system == "Linux":
        candidates.extend(LINUX_STABLE_CHROME_CANDIDATES)
        for command in ("google-chrome-stable", "google-chrome"):
            resolved = shutil.which(command)
            if resolved:
                candidates.append(Path(resolved))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        marker = str(candidate)
        if marker not in seen:
            seen.add(marker)
            unique.append(candidate)
    return unique


def find_stable_chrome_binary(*, system: str | None = None) -> Path | None:
    for candidate in chrome_binary_candidates(system=system):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def default_stable_chrome_binary(*, system: str | None = None) -> Path:
    found = find_stable_chrome_binary(system=system)
    if found is not None:
        return found
    current_system = system or platform.system()
    if current_system == "Linux":
        return LINUX_STABLE_CHROME_CANDIDATES[0]
    return MACOS_STABLE_CHROME


def chrome_runtime_executables(binary: str | Path) -> set[Path]:
    """Return exact main-process executables produced by a Chrome launcher.

    The Linux ``google-chrome-stable`` entrypoint is a shell launcher which
    execs the sibling ``chrome`` ELF binary. Both are part of the same signed
    installation and are accepted; arbitrary Chromium executables are not.
    """
    configured = Path(binary).expanduser()
    candidates = {configured}
    try:
        resolved = configured.resolve(strict=False)
    except OSError:
        resolved = configured
    candidates.add(resolved)
    if platform.system() == "Linux" and resolved.name in {
        "google-chrome",
        "google-chrome-stable",
    }:
        candidates.add(resolved.with_name("chrome"))
    return candidates


def chrome_main_process_matches(command_line: str, binary: str | Path) -> bool:
    """Match a Chrome main process by exact executable, not a broad name."""
    command = command_line.strip()
    if (
        not command
        or " -lc " in command
        or " -c " in command
        or " --type=" in command
    ):
        return False
    for executable in chrome_runtime_executables(binary):
        executable_text = str(executable)
        if command == executable_text or command.startswith(f"{executable_text} "):
            return True

    # Preserve the existing macOS test/runtime contract where the configured
    # executable may be patched after import. Helpers are still excluded by
    # requiring the exact app main-executable layout.
    return (
        "/Google Chrome.app/Contents/MacOS/Google Chrome " in f"{command} "
        or "/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing "
        in f"{command} "
    )


def pid_namespace_matches_current(pid: int) -> bool:
    """Return whether ``pid`` belongs to this process's PID namespace.

    A native Linux worker can see container processes through the host ``/proc``.
    Profile paths are intentionally identical across native and appliance
    environments, so path/UID/executable matching alone is not sufficient to
    prove ownership. Non-Linux platforms do not expose Linux PID namespaces.
    """
    if platform.system() != "Linux":
        return True
    if pid <= 0:
        return False
    try:
        return _namespace_identity("/proc/self/ns/pid") == _namespace_identity(
            f"/proc/{pid}/ns/pid"
        )
    except OSError:
        return False


def _namespace_identity(path: str) -> tuple[int, int]:
    stat = os.stat(path)
    return stat.st_dev, stat.st_ino
