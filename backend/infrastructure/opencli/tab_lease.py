"""Explicit ownership for deterministic OpenCLI ``site:<site>`` tabs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import fcntl
import hashlib
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable, Iterator
from uuid import uuid4

from backend.infrastructure.browser import session_state
from backend.domain.failures import OpenCLISiteLeaseTimeout
from backend.infrastructure.opencli.commands import close_site_session
from backend.infrastructure.opencli.runtime import _opencli_lock_timeout
from backend.infrastructure.runtime.environment import state_home
from backend.infrastructure.observability.logging import get_logger


logger = get_logger("opencli.tab_lease")


OPENCLI_PROFILE_ALIAS = "infoos-fetch"
_SITE_ALIASES = {"x": "twitter", "twitter": "twitter", "xhs": "xiaohongshu", "rednote": "xiaohongshu"}
_SAFE_SITE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()
_PROFILE_LOCK_GUARD = threading.Lock()
_PROFILE_LOCK_PID = 0
_PROFILE_RW_LOCK: "_ThreadReaderWriterLock | None" = None

LeaseEventSink = Callable[[str, dict[str, str]], None]
_event_sink: ContextVar[LeaseEventSink | None] = ContextVar("opencli_lease_event_sink", default=None)
_active_leases: ContextVar[tuple["OpenCLITabLease", ...]] = ContextVar("active_opencli_tab_leases", default=())
_profile_read_depth: ContextVar[int] = ContextVar("opencli_profile_read_depth", default=0)
_profile_write_depth: ContextVar[int] = ContextVar("opencli_profile_write_depth", default=0)


@dataclass(frozen=True)
class OpenCLITabLease:
    lease_id: str
    context_id: str
    normalized_site: str
    profile_alias: str = OPENCLI_PROFILE_ALIAS


def normalize_opencli_site(site: str) -> str:
    normalized = _SITE_ALIASES.get(str(site).strip().lower(), str(site).strip().lower())
    if not _SAFE_SITE_RE.fullmatch(normalized):
        raise ValueError(f"invalid OpenCLI site: {site!r}")
    return normalized


def current_opencli_context_id() -> str:
    state = session_state.read()
    context_id = state.context_id if state is not None else None
    return str(context_id).strip() if context_id else OPENCLI_PROFILE_ALIAS


@contextmanager
def bind_opencli_lease_event_sink(sink: LeaseEventSink | None) -> Iterator[None]:
    token = _event_sink.set(sink)
    try:
        yield
    finally:
        _event_sink.reset(token)


def current_opencli_lease_event_sink() -> LeaseEventSink | None:
    """Capture only the event sink for a child worker, never an active lease."""
    return _event_sink.get()


@contextmanager
def opencli_profile_write_lock(
    *,
    timeout_seconds: float | None = None,
) -> Iterator[None]:
    """Exclude all site commands while profile prepare/repair/cleanup mutates state."""
    if _profile_write_depth.get() > 0:
        token = _profile_write_depth.set(_profile_write_depth.get() + 1)
        try:
            yield
        finally:
            _profile_write_depth.reset(token)
        return
    if _profile_read_depth.get() > 0:
        raise RuntimeError("cannot mutate the OpenCLI profile while holding a site lease")

    timeout = _opencli_lock_timeout() if timeout_seconds is None else max(0.0, timeout_seconds)
    deadline = time.monotonic() + timeout
    rw_lock = _profile_thread_lock()
    if not rw_lock.acquire_write(timeout):
        raise TimeoutError("Timed out waiting for OpenCLI profile write lease")
    try:
        with _profile_lock_path().open("a+", encoding="utf-8") as handle:
            _acquire_file_lock_mode(
                handle,
                deadline=deadline,
                mode=fcntl.LOCK_EX,
                description="profile write lease",
            )
            token = _profile_write_depth.set(1)
            try:
                yield
            finally:
                _profile_write_depth.reset(token)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        rw_lock.release_write()


@contextmanager
def opencli_tab_lease(
    site: str,
    *,
    executable: str = "opencli",
    timeout_seconds: float | None = None,
    close_tabs: bool = True,
) -> Iterator[OpenCLITabLease]:
    """Acquire one reentrant, cross-process lease and optionally close its site tab."""
    normalized_site = normalize_opencli_site(site)
    context_id = current_opencli_context_id()
    key = f"{context_id}\0{normalized_site}"
    for active in reversed(_active_leases.get()):
        if active.context_id == context_id and active.normalized_site == normalized_site:
            yield active
            return

    lease = OpenCLITabLease(uuid4().hex, context_id, normalized_site)
    timeout = _opencli_lock_timeout() if timeout_seconds is None else max(0.0, timeout_seconds)
    deadline = time.monotonic() + timeout
    with _opencli_profile_read_lock(deadline=deadline):
        lock_path = _lease_lock_path(key)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        thread_lock = _thread_lock(key)
        remaining = max(0.0, deadline - time.monotonic())
        acquired = thread_lock.acquire(timeout=remaining)
        if not acquired:
            raise OpenCLISiteLeaseTimeout(normalized_site)
        try:
            with lock_path.open("a+", encoding="utf-8") as handle:
                _acquire_file_lock(handle, deadline=deadline, site=normalized_site)
                try:
                    if close_tabs:
                        _close_and_record(lease, executable=executable, phase="stale")
                    _record("opencli_tab_lease_acquired", lease, phase="acquire", close_status="not_applicable")
                    token = _active_leases.set((*_active_leases.get(), lease))
                    try:
                        yield lease
                    finally:
                        _active_leases.reset(token)
                        if close_tabs:
                            _close_and_record(lease, executable=executable, phase="finally")
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            thread_lock.release()


def _close_and_record(lease: OpenCLITabLease, *, executable: str, phase: str) -> None:
    try:
        result = close_site_session(
            lease.normalized_site,
            profile_alias=lease.profile_alias,
            executable=executable,
        )
    except Exception as exc:  # noqa: BLE001 - OpenCLI subprocess cleanup must never replace the business result
        _record(
            "opencli_tab_close_failed",
            lease,
            phase=phase,
            close_status="exception",
            error_class=type(exc).__name__,
        )
        return
    if result.returncode == 0:
        event_type = "opencli_tab_stale_close_completed" if phase == "stale" else "opencli_tab_close_completed"
        _record(event_type, lease, phase=phase, close_status="closed")
    else:
        _record(
            "opencli_tab_close_failed",
            lease,
            phase=phase,
            close_status=f"exit_{result.returncode}",
            error_class="OpenCLICommandError",
        )


def _record(event_type: str, lease: OpenCLITabLease, **payload: str) -> None:
    sink = _event_sink.get()
    if sink is None:
        return
    compact = {
        "lease_id": lease.lease_id,
        "normalized_site": lease.normalized_site,
        **payload,
    }
    try:
        sink(event_type, compact)
    except (OSError, sqlite3.Error):
        logger.exception("opencli lease event persistence failed")


def _lease_lock_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return state_home() / "opencli-tab-leases" / f"{digest}.lock"


def _profile_lock_path() -> Path:
    path = _lease_lock_path("profile\0infoos-fetch")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _thread_lock(key: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _acquire_file_lock(handle, *, deadline: float, site: str) -> None:  # type: ignore[no-untyped-def]
    try:
        _acquire_file_lock_mode(
            handle,
            deadline=deadline,
            mode=fcntl.LOCK_EX,
            description=f"site lease: {site}",
        )
    except TimeoutError as exc:
        raise OpenCLISiteLeaseTimeout(site) from exc


def _acquire_file_lock_mode(
    handle,  # type: ignore[no-untyped-def]
    *,
    deadline: float,
    mode: int,
    description: str,
) -> None:
    while True:
        try:
            fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for OpenCLI {description}")
            time.sleep(0.2)


@contextmanager
def _opencli_profile_read_lock(*, deadline: float) -> Iterator[None]:
    if _profile_write_depth.get() > 0 or _profile_read_depth.get() > 0:
        token = _profile_read_depth.set(_profile_read_depth.get() + 1)
        try:
            yield
        finally:
            _profile_read_depth.reset(token)
        return

    rw_lock = _profile_thread_lock()
    if not rw_lock.acquire_read(max(0.0, deadline - time.monotonic())):
        raise TimeoutError("Timed out waiting for OpenCLI profile read lease")
    try:
        with _profile_lock_path().open("a+", encoding="utf-8") as handle:
            _acquire_file_lock_mode(
                handle,
                deadline=deadline,
                mode=fcntl.LOCK_SH,
                description="profile read lease",
            )
            token = _profile_read_depth.set(1)
            try:
                yield
            finally:
                _profile_read_depth.reset(token)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        rw_lock.release_read()


def _profile_thread_lock() -> "_ThreadReaderWriterLock":
    import os

    global _PROFILE_LOCK_PID, _PROFILE_RW_LOCK
    pid = os.getpid()
    with _PROFILE_LOCK_GUARD:
        if _PROFILE_RW_LOCK is None or _PROFILE_LOCK_PID != pid:
            _PROFILE_RW_LOCK = _ThreadReaderWriterLock()
            _PROFILE_LOCK_PID = pid
        return _PROFILE_RW_LOCK


class _ThreadReaderWriterLock:
    """Writer-preferring in-process half of the profile flock contract."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    def acquire_read(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._writer or self._writers_waiting:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._condition.wait(remaining):
                    return False
            self._readers += 1
            return True

    def release_read(self) -> None:
        with self._condition:
            self._readers -= 1
            if self._readers == 0:
                self._condition.notify_all()

    def acquire_write(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._condition:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not self._condition.wait(remaining):
                        return False
                self._writer = True
                return True
            finally:
                self._writers_waiting -= 1

    def release_write(self) -> None:
        with self._condition:
            self._writer = False
            self._condition.notify_all()
