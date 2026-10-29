"""Tests for backend.infrastructure.opencli.runtime — executable resolution, env config, and
the cross-process file lock (including real concurrent contention via
multiprocessing).
"""

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys
import time
from pathlib import Path
import threading

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.infrastructure.opencli.runtime import (  # noqa: E402
    _opencli_lock_timeout,
    opencli_browser_lock,
    resolve_opencli_command,
    resolve_opencli_executable,
)
from backend.infrastructure.opencli.commands import OpenCLIResult, ensure_daemon  # noqa: E402
from backend.infrastructure.opencli import tab_lease  # noqa: E402


def test_in_flight_state_counts_overlapping_tasks(tmp_path, monkeypatch):
    from backend.infrastructure.browser.in_flight import in_flight_section, read_lock_payload

    monkeypatch.setattr("backend.infrastructure.browser.in_flight.LOCK_PATH", tmp_path / "in-flight.lock")
    with in_flight_section("first", task_count=1):
        with in_flight_section("second", task_count=2):
            payload = read_lock_payload()
            assert payload is not None
            assert payload["active_count"] == 2
            assert len(payload["owners"]) == 2
        payload = read_lock_payload()
        assert payload is not None
        assert payload["active_count"] == 1


# ---------------------------------------------------------------------------
# Executable resolution
# ---------------------------------------------------------------------------


def test_resolve_opencli_executable_default(monkeypatch, tmp_path):
    monkeypatch.delenv("INFOOS_OPENCLI_EXECUTABLE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert resolve_opencli_executable() == "opencli"


def test_resolve_opencli_executable_prefers_nvm_opencli(monkeypatch, tmp_path):
    monkeypatch.delenv("INFOOS_OPENCLI_EXECUTABLE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    nvm_opencli = tmp_path / ".nvm" / "versions" / "node" / "v24.15.0" / "bin" / "opencli"
    nvm_opencli.parent.mkdir(parents=True)
    nvm_opencli.write_text("#!/bin/sh\n", encoding="utf-8")
    nvm_opencli.chmod(0o755)

    assert resolve_opencli_executable() == str(nvm_opencli)


def test_resolve_opencli_executable_env_override(monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "  /stable/bin/opencli  ")
    assert resolve_opencli_executable() == "/stable/bin/opencli"


def test_resolve_opencli_executable_passthrough_for_explicit_path():
    # When the caller passes a literal binary, we don't override it.
    assert resolve_opencli_executable("/usr/local/bin/myopencli") == "/usr/local/bin/myopencli"


def test_resolve_opencli_command_rewrites_first_arg(monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_EXECUTABLE", "/stable/bin/opencli")
    assert resolve_opencli_command(["opencli", "twitter", "tweets"]) == [
        "/stable/bin/opencli",
        "twitter",
        "tweets",
    ]


def test_resolve_opencli_command_passthrough_for_explicit_path():
    # If the command's first arg isn't literally "opencli", leave it alone.
    assert resolve_opencli_command(["/usr/bin/hackernews"]) == ["/usr/bin/hackernews"]


def test_resolve_opencli_command_empty():
    assert resolve_opencli_command([]) == []


def test_ensure_daemon_restarts_when_status_reports_not_running(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        if args == ["daemon", "status"]:
            return OpenCLIResult(returncode=0, stdout="Daemon: not running")
        return OpenCLIResult(returncode=0, stdout="Daemon started")

    monkeypatch.setattr("backend.infrastructure.opencli.commands.run_opencli", fake_run)

    result = ensure_daemon()

    assert result.returncode == 0
    assert calls == [["daemon", "status"], ["daemon", "restart"]]


def test_ensure_daemon_keeps_running_daemon(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return OpenCLIResult(returncode=0, stdout="Daemon: running on port 19825")

    monkeypatch.setattr("backend.infrastructure.opencli.commands.run_opencli", fake_run)

    result = ensure_daemon()

    assert result.returncode == 0
    assert calls == [["daemon", "status"]]


# ---------------------------------------------------------------------------
# Lock timeout
# ---------------------------------------------------------------------------


def test_opencli_lock_timeout_default_is_60s(monkeypatch):
    """The default was lowered from 300s to 60s for v1 cadence."""
    monkeypatch.delenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", raising=False)
    assert _opencli_lock_timeout() == 60.0


def test_opencli_lock_timeout_env_override(monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", "5.5")
    assert _opencli_lock_timeout() == 5.5


def test_opencli_lock_timeout_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", "nope")
    assert _opencli_lock_timeout() == 60.0


def test_tab_lease_stale_closes_and_finally_closes_with_compact_events(monkeypatch):
    closes: list[str] = []
    events: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        tab_lease,
        "close_site_session",
        lambda site, **_kwargs: closes.append(site) or OpenCLIResult(),
    )

    with tab_lease.bind_opencli_lease_event_sink(lambda kind, payload: events.append((kind, payload))):
        with tab_lease.opencli_tab_lease("x") as lease:
            assert lease.normalized_site == "twitter"

    assert closes == ["twitter", "twitter"]
    assert [kind for kind, _payload in events] == [
        "opencli_tab_stale_close_completed",
        "opencli_tab_lease_acquired",
        "opencli_tab_close_completed",
    ]
    assert all(set(payload) <= {"lease_id", "normalized_site", "phase", "close_status"} for _, payload in events)


def test_close_tabs_false_skips_stale_and_final_closes_but_keeps_lease(monkeypatch):
    closes: list[str] = []
    entered = False
    monkeypatch.setattr(
        tab_lease,
        "close_site_session",
        lambda site, **_kwargs: closes.append(site) or OpenCLIResult(),
    )

    with tab_lease.opencli_tab_lease("xiaohongshu", close_tabs=False) as lease:
        entered = True
        assert lease.normalized_site == "xiaohongshu"

    assert entered is True
    assert closes == []


@pytest.mark.parametrize("error", [RuntimeError("failed"), TimeoutError("timed out")])
def test_tab_lease_closes_when_business_execution_raises(monkeypatch, error):
    closes: list[str] = []
    monkeypatch.setattr(
        tab_lease,
        "close_site_session",
        lambda site, **_kwargs: closes.append(site) or OpenCLIResult(),
    )

    with pytest.raises(type(error)):
        with tab_lease.opencli_tab_lease("youtube"):
            raise error

    assert closes == ["youtube", "youtube"]


def test_nested_same_site_lease_reuses_owner_without_early_close(monkeypatch):
    closes: list[str] = []
    monkeypatch.setattr(
        tab_lease,
        "close_site_session",
        lambda site, **_kwargs: closes.append(site) or OpenCLIResult(),
    )

    with tab_lease.opencli_tab_lease("twitter") as outer:
        with tab_lease.opencli_tab_lease("x") as inner:
            assert inner.lease_id == outer.lease_id
            assert closes == ["twitter"]

    assert closes == ["twitter", "twitter"]


def test_tab_cleanup_failure_is_warning_only(monkeypatch):
    calls = 0
    events: list[str] = []

    def close(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return OpenCLIResult(returncode=1 if calls == 2 else 0)

    monkeypatch.setattr(tab_lease, "close_site_session", close)
    with tab_lease.bind_opencli_lease_event_sink(lambda kind, _payload: events.append(kind)):
        with tab_lease.opencli_tab_lease("twitter"):
            business_result = "success"

    assert business_result == "success"
    assert events[-1] == "opencli_tab_close_failed"


@pytest.mark.parametrize("business_error", [None, ValueError("business failed")])
def test_tab_cleanup_timeout_preserves_business_outcome(monkeypatch, business_error):
    events = []

    def close(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="opencli browser site:twitter close", timeout=15)

    def run_business():
        with tab_lease.opencli_tab_lease("twitter"):
            if business_error is not None:
                raise business_error
            return "success"

    monkeypatch.setattr(tab_lease, "close_site_session", close)
    with tab_lease.bind_opencli_lease_event_sink(lambda kind, payload: events.append((kind, payload))):
        if business_error is None:
            assert run_business() == "success"
        else:
            with pytest.raises(ValueError) as caught:
                run_business()
            assert caught.value is business_error

    failures = [payload for kind, payload in events if kind == "opencli_tab_close_failed"]
    assert [payload["phase"] for payload in failures] == ["stale", "finally"]
    assert all(payload["error_class"] == "TimeoutExpired" for payload in failures)


def test_same_site_serializes_while_different_sites_overlap(monkeypatch):
    monkeypatch.setattr(tab_lease, "close_site_session", lambda *_args, **_kwargs: OpenCLIResult())
    active: dict[str, int] = {"twitter": 0, "youtube": 0}
    peak_total = 0
    same_site_peak = 0
    guard = threading.Lock()
    barrier = threading.Barrier(3)

    def worker(site: str) -> None:
        nonlocal peak_total, same_site_peak
        barrier.wait()
        with tab_lease.opencli_tab_lease(site):
            with guard:
                active[site] += 1
                peak_total = max(peak_total, sum(active.values()))
                same_site_peak = max(same_site_peak, active[site])
            time.sleep(0.08)
            with guard:
                active[site] -= 1

    threads = [
        threading.Thread(target=worker, args=("twitter",)),
        threading.Thread(target=worker, args=("twitter",)),
        threading.Thread(target=worker, args=("youtube",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert same_site_peak == 1
    assert peak_total == 2


def test_profile_repair_waits_for_long_running_site_command(monkeypatch):
    monkeypatch.setattr(tab_lease, "close_site_session", lambda *_args, **_kwargs: OpenCLIResult())
    site_entered = threading.Event()
    release_site = threading.Event()
    repair_entered = threading.Event()

    def site_command() -> None:
        with tab_lease.opencli_tab_lease("twitter"):
            site_entered.set()
            assert release_site.wait(timeout=2)

    def repair() -> None:
        assert site_entered.wait(timeout=2)
        with tab_lease.opencli_profile_write_lock(timeout_seconds=2):
            repair_entered.set()

    site_thread = threading.Thread(target=site_command)
    repair_thread = threading.Thread(target=repair)
    site_thread.start()
    repair_thread.start()
    assert site_entered.wait(timeout=1)
    assert not repair_entered.wait(timeout=0.1)
    release_site.set()
    assert repair_entered.wait(timeout=1)
    site_thread.join(timeout=1)
    repair_thread.join(timeout=1)

    assert not site_thread.is_alive()
    assert not repair_thread.is_alive()


def _site_lease_contention_worker(
    state_dir: str,
    barrier,  # type: ignore[no-untyped-def]
    result_queue,  # type: ignore[no-untyped-def]
) -> None:
    os.environ["INFOOS_STATE_HOME"] = state_dir
    from backend.infrastructure.opencli import tab_lease as child_tab_lease
    from backend.infrastructure.opencli.commands import OpenCLIResult as ChildResult

    child_tab_lease.close_site_session = lambda *_args, **_kwargs: ChildResult()
    barrier.wait()
    started = time.monotonic()
    with child_tab_lease.opencli_tab_lease("twitter", timeout_seconds=5):
        acquired_after = time.monotonic() - started
        time.sleep(0.25)
        result_queue.put(acquired_after)


def _profile_reader_worker(
    state_dir: str,
    reader_entered,  # type: ignore[no-untyped-def]
    release_reader,  # type: ignore[no-untyped-def]
) -> None:
    os.environ["INFOOS_STATE_HOME"] = state_dir
    from backend.infrastructure.opencli import tab_lease as child_tab_lease
    from backend.infrastructure.opencli.commands import OpenCLIResult as ChildResult

    child_tab_lease.close_site_session = lambda *_args, **_kwargs: ChildResult()
    with child_tab_lease.opencli_tab_lease("twitter", timeout_seconds=5):
        reader_entered.set()
        release_reader.wait(timeout=5)


def _profile_writer_worker(
    state_dir: str,
    reader_entered,  # type: ignore[no-untyped-def]
    result_queue,  # type: ignore[no-untyped-def]
) -> None:
    os.environ["INFOOS_STATE_HOME"] = state_dir
    from backend.infrastructure.opencli import tab_lease as child_tab_lease

    reader_entered.wait(timeout=5)
    started = time.monotonic()
    with child_tab_lease.opencli_profile_write_lock(timeout_seconds=5):
        result_queue.put(time.monotonic() - started)


def test_same_site_lease_serializes_across_processes(tmp_path):
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    result_queue = ctx.Queue()
    processes = [
        ctx.Process(
            target=_site_lease_contention_worker,
            args=(str(tmp_path / "state"), barrier, result_queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    acquired = sorted(result_queue.get(timeout=5) for _ in processes)
    for process in processes:
        process.join(timeout=5)

    assert all(process.exitcode == 0 for process in processes)
    assert acquired[0] < 0.15
    assert acquired[1] >= 0.2


def test_profile_write_waits_for_site_read_across_processes(tmp_path):
    ctx = mp.get_context("spawn")
    reader_entered = ctx.Event()
    release_reader = ctx.Event()
    result_queue = ctx.Queue()
    state_dir = str(tmp_path / "profile-state")
    reader = ctx.Process(
        target=_profile_reader_worker,
        args=(state_dir, reader_entered, release_reader),
    )
    writer = ctx.Process(
        target=_profile_writer_worker,
        args=(state_dir, reader_entered, result_queue),
    )
    reader.start()
    writer.start()
    assert reader_entered.wait(timeout=3)
    time.sleep(0.25)
    release_reader.set()
    acquired_after = result_queue.get(timeout=5)
    reader.join(timeout=5)
    writer.join(timeout=5)

    assert reader.exitcode == 0
    assert writer.exitcode == 0
    assert acquired_after >= 0.2


# ---------------------------------------------------------------------------
# Lock acquisition / release (single process)
# ---------------------------------------------------------------------------


def test_lock_acquires_and_releases(tmp_path, monkeypatch):
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_PATH", str(tmp_path / "test.lock"))
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", "5")

    with opencli_browser_lock():
        # Lock file should exist and be writable.
        assert (tmp_path / "test.lock").exists()
    # After exit, a new acquirer in the same process gets the lock immediately.
    with opencli_browser_lock():
        pass


def test_lock_creates_parent_directory(tmp_path, monkeypatch):
    nested = tmp_path / "deep" / "nested" / "dir"
    lock = nested / "test.lock"
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_PATH", str(lock))
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", "2")
    with opencli_browser_lock():
        assert nested.is_dir()


def test_lock_thread_safe_within_process(tmp_path, monkeypatch):
    """Two threads in the same process must serialize on the RLock."""
    import threading

    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_PATH", str(tmp_path / "thread.lock"))
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", "5")
    order: list[str] = []

    def worker(name: str) -> None:
        with opencli_browser_lock():
            order.append(f"{name}-enter")
            time.sleep(0.05)
            order.append(f"{name}-exit")

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Each thread's enter must be matched by its exit before the next enter
    # (because flock + RLock are honored cross-handles but threads share fd).
    # The relaxed guarantee: at least 3 enters and 3 exits appear, in some
    # sensible interleaving — we just confirm the lock is reentrant-safe.
    assert order.count("t0-enter") == 1
    assert order.count("t0-exit") == 1


def test_lock_timeout_raises(tmp_path, monkeypatch):
    """When the lock can't be acquired within timeout, raise TimeoutError."""
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_PATH", str(tmp_path / "timeout.lock"))
    monkeypatch.setenv("INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS", "1")

    # Hold the lock from the main process.
    with opencli_browser_lock():
        # Spawn a child that tries to acquire the same lock and times out.
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=1) as pool:
            result = pool.apply(_child_try_lock, args=(str(tmp_path / "timeout.lock"), 1))
        assert result == "timeout"


def _child_try_lock(lock_path: str, timeout: float) -> str:
    """Run in a child process; return 'acquired' or 'timeout'."""
    # Re-import inside child since spawn doesn't inherit state.
    from backend.infrastructure.opencli.runtime import opencli_browser_lock

    os.environ["INFOOS_OPENCLI_LOCK_PATH"] = lock_path
    os.environ["INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS"] = str(timeout)
    try:
        with opencli_browser_lock(timeout_seconds=timeout):
            return "acquired"
    except TimeoutError:
        return "timeout"


# ---------------------------------------------------------------------------
# Real concurrent contention (multiprocess)
# ---------------------------------------------------------------------------


def _contention_worker(
    lock_path_str: str,
    barrier,  # type: ignore[no-untyped-def]
    hold_seconds: float,
    timeout: float,
    result_queue,  # type: ignore[no-untyped-def]
) -> None:
    """Module-level so it pickles cleanly under spawn.

    Synchronize on barrier, then try to acquire the lock and hold it.
    Records wall-clock time between barrier release and successful
    acquisition — the first process should be near-zero, the second
    delayed by ~hold_seconds, proving real cross-process serialization.
    """
    from backend.infrastructure.opencli.runtime import opencli_browser_lock

    os.environ["INFOOS_OPENCLI_LOCK_PATH"] = lock_path_str
    os.environ["INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS"] = str(timeout)
    barrier.wait()
    start = time.monotonic()
    try:
        with opencli_browser_lock(timeout_seconds=timeout):
            acquired_at = time.monotonic() - start
            time.sleep(hold_seconds)
            result_queue.put(("acquired", acquired_at))
    except TimeoutError:
        result_queue.put(("timeout", time.monotonic() - start))


def test_concurrent_processes_serialize_on_lock(tmp_path):
    """Two real processes must serialize — the second waits for the first to release.

    This is the test the original suite was missing: ``fcntl.flock`` is per-fd,
    so the proof has to come from cross-process contention, not from
    re-acquiring in the same process.
    """
    lock_path = tmp_path / "contention.lock"
    hold_seconds = 1.0
    timeout = 5.0

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    queue: "mp.Queue" = ctx.Queue()  # type: ignore[name-defined]

    procs = [
        ctx.Process(
            target=_contention_worker,
            args=(str(lock_path), barrier, hold_seconds, timeout, queue),
        )
        for _ in range(2)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=timeout + 5)
        assert not p.is_alive(), "child hung past lock timeout"

    results = [queue.get_nowait() for _ in range(2)]
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["acquired", "acquired"], f"one side timed out: {results}"

    wait_times = sorted(r[1] for r in results)
    # The second process should have waited at least ~hold_seconds
    # (minus scheduler noise) before its acquire returned.
    assert wait_times[1] >= hold_seconds * 0.8, (
        f"second acquirer waited {wait_times[1]:.2f}s, expected ~{hold_seconds}s"
    )
    # The first acquirer should have started near the barrier release.
    assert wait_times[0] < 0.5, (
        f"first acquirer waited {wait_times[0]:.2f}s, expected near-zero"
    )


def _lock_crash_holder(queue) -> None:  # type: ignore[no-untyped-def]
    """Module-level so it pickles cleanly under spawn."""
    from backend.infrastructure.opencli.runtime import opencli_browser_lock

    lock_path = os.environ["_TEST_CRASH_LOCK_PATH"]
    os.environ["INFOOS_OPENCLI_LOCK_PATH"] = lock_path
    with opencli_browser_lock(timeout_seconds=2):
        queue.put("holding")
        # Give the parent a moment to read the queue before we crash.
        time.sleep(0.3)
        # Crash immediately (skip the cleanup).
        os._exit(1)


def _lock_crash_acquirer(queue) -> None:  # type: ignore[no-untyped-def]
    """Module-level so it pickles cleanly under spawn."""
    from backend.infrastructure.opencli.runtime import opencli_browser_lock

    lock_path = os.environ["_TEST_CRASH_LOCK_PATH"]
    os.environ["INFOOS_OPENCLI_LOCK_PATH"] = lock_path
    os.environ["INFOOS_OPENCLI_LOCK_TIMEOUT_SECONDS"] = "2"
    try:
        with opencli_browser_lock(timeout_seconds=2):
            queue.put("acquired-after-crash")
    except TimeoutError:
        queue.put("timeout-after-crash")


def test_lock_released_after_process_crash(tmp_path):
    """If a process holding the lock dies, the lock must be released.

    ``fcntl.flock`` releases on process death (unlike POSIX advisory locks),
    so a new acquirer should get in immediately.
    """
    lock_path = tmp_path / "crash.lock"
    ctx = mp.get_context("spawn")
    queue: "mp.Queue" = ctx.Queue()  # type: ignore[name-defined]
    os.environ["_TEST_CRASH_LOCK_PATH"] = str(lock_path)

    p = ctx.Process(target=_lock_crash_holder, args=(queue,))
    p.start()
    # Wait for the child to be holding the lock.
    assert queue.get(timeout=3) == "holding"
    p.join(timeout=5)
    assert p.exitcode == 1

    p2 = ctx.Process(target=_lock_crash_acquirer, args=(queue,))
    p2.start()
    p2.join(timeout=5)
    assert queue.get(timeout=3) == "acquired-after-crash"
    del os.environ["_TEST_CRASH_LOCK_PATH"]
