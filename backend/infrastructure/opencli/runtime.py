"""opencli_runtime — OpenCLI subprocess wrapping for InfoOS.

opencli_runtime — subpackage migrated to infoos in PR 4.1.
The corresponding backend/infrastructure/opencli/runtime.py was deleted in this commit.
This file is now the sole source of truth.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import threading
import time


_OPENCLI_THREAD_LOCK = threading.RLock()


def resolve_opencli_executable(executable: str = "opencli") -> str:
    """Resolve the OpenCLI executable consistently across services and shells."""
    if executable != "opencli":
        return executable
    configured = os.environ.get("INFOOS_OPENCLI_EXECUTABLE", "").strip()
    if configured:
        return configured
    nvm_opencli = _preferred_nvm_opencli()
    return str(nvm_opencli) if nvm_opencli else executable


def _preferred_nvm_opencli() -> Path | None:
    nvm_versions = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_versions.exists():
        return None
    candidates = sorted(nvm_versions.glob("*/bin/opencli"), reverse=True)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_opencli_command(command: list[str]) -> list[str]:
    if not command:
        return command
    if command[0] == "opencli":
        return [resolve_opencli_executable(command[0]), *command[1:]]
    return command


def _opencli_lock_timeout(default: float = 60.0) -> float:
    """Resolve lock timeout. Default lowered from 300s to 60s for v1 cadence.

    The 300s default was originally conservative, but in practice a hung
    daemon does not recover within 5 minutes — it just blocks the ingestion
    pipeline. 60s is the longest we should ever wait; if the bridge hasn't
    come back by then, escalate to ``prepare-browser-session`` repair.
    """
    raw_value = os.environ.get("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", str(default)).strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return default


def _opencli_lock_path() -> Path:
    from backend.infrastructure.runtime.environment import opencli_browser_lock_path
    return opencli_browser_lock_path()


@contextmanager
def opencli_browser_lock(timeout_seconds: float | None = None):
    """Serialize profile management with one deadline for thread and file waits."""
    timeout = _opencli_lock_timeout() if timeout_seconds is None else max(0.0, timeout_seconds)
    lock_path = _opencli_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    error = f"Timed out waiting for OpenCLI browser lock: {lock_path}"
    if not _OPENCLI_THREAD_LOCK.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError(error)
    try:
        if timeout > 0 and time.monotonic() >= deadline:
            raise TimeoutError(error)
        with lock_path.open("a+", encoding="utf-8") as handle:
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(error)
                    time.sleep(min(0.2, remaining))
                    if time.monotonic() >= deadline:
                        raise TimeoutError(error)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        _OPENCLI_THREAD_LOCK.release()
