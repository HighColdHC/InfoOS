"""Cross-process serialization for GPU-backed inference."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
import time
from collections.abc import Iterator
from pathlib import Path

from backend.infrastructure.runtime.environment import gpu_lock_path


class GpuLockTimeoutError(TimeoutError):
    """Raised when the shared GPU remains busy beyond the configured timeout."""


@contextmanager
def gpu_lock(*, timeout_seconds: float | None = None) -> Iterator[Path]:
    path = gpu_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    timeout = timeout_seconds if timeout_seconds is not None else _timeout_from_env()
    deadline = time.monotonic() + max(float(timeout), 0.0)
    with path.open("a+") as handle:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise GpuLockTimeoutError(
                        f"timed out after {timeout:g}s waiting for GPU lock {path}"
                    )
                time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            yield path
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _timeout_from_env() -> float:
    try:
        value = float(os.environ.get("INFOOS_GPU_LOCK_TIMEOUT_SECONDS", "1800"))
    except ValueError:
        return 1800.0
    return value if value >= 0 else 1800.0
