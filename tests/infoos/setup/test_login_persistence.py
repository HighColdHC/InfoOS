from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import threading
import time

import pytest

from backend.application.setup.login_persistence import verify_login_persistence
from backend.domain.sources.registry import source_browser_platforms
from backend.infrastructure.browser import session_state
from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus, SetupState, load_state, save_state


def _binding(profile: Path, pid: int) -> session_state.BrowserSessionState:
    return session_state.BrowserSessionState(
        schema_version=2,
        profile_dir=str(profile),
        context_id=f"ctx-{pid}",
        alias="infoos-fetch",
        chrome_pid=pid,
        binding_proof=session_state.BINDING_PROOF,
        started_at="2026-07-22T00:00:00Z",
        last_health_at="2026-07-22T00:00:00Z",
        last_health_status="ready",
    )


@pytest.mark.parametrize("platform", sorted(source_browser_platforms()))
def test_all_source_browser_platforms_require_and_record_full_restart_proof(
    platform,
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "chrome-profile"
    state_path = tmp_path / "setup-state.json"
    bindings = iter([_binding(profile, 101), _binding(profile, 202)])
    probe_calls = []
    monkeypatch.setattr(
        "backend.application.setup.login_persistence.chrome_profile_dir",
        lambda: profile,
    )

    result = verify_login_persistence(
        state_path,
        platform,
        probe=lambda value: probe_calls.append(value) or AccountState(
            status=AccountStatus.VERIFIED,
            last_checked="2026-07-22T00:00:00Z",
        ),
        restart=lambda: {"ok": True},
        cookie_check=lambda value, checked_profile: {
            "platform": value,
            "status": "logged_in",
            "profile": str(checked_profile),
        },
        read_binding=lambda: next(bindings),
        profile_write_lock=nullcontext,
        browser_lock=nullcontext,
    )

    assert result["ok"] is True
    assert result["platform"] == platform
    assert result["status"] == "verified"
    assert result["persisted"] is True
    assert result["persistence_stage"] == "verified"
    assert probe_calls == [platform, platform]
    stored = load_state(state_path).accounts[platform]
    assert stored.persisted is True
    assert stored.persistence_checked_at is not None


def test_restart_failure_is_safe_and_persists_false(tmp_path, monkeypatch):
    profile = tmp_path / "chrome-profile"
    state_path = tmp_path / "setup-state.json"
    monkeypatch.setattr(
        "backend.application.setup.login_persistence.chrome_profile_dir",
        lambda: profile,
    )

    result = verify_login_persistence(
        state_path,
        "x",
        probe=lambda _platform: AccountState(status=AccountStatus.VERIFIED),
        restart=lambda: {"ok": False, "error": "cookie=do-not-leak"},
        read_binding=lambda: _binding(profile, 101),
        profile_write_lock=nullcontext,
        browser_lock=nullcontext,
    )

    assert result["ok"] is False
    assert result["persisted"] is False
    assert result["persistence_stage"] == "restart"
    assert "do-not-leak" not in result["error"]
    stored = load_state(state_path).accounts["x"]
    assert stored.persisted is False
    assert stored.persistence_error == "Dedicated Chrome did not restart gracefully"


@pytest.mark.parametrize(
    ("after", "cookie_status", "post_status", "expected_stage"),
    [
        (None, "logged_in", AccountStatus.VERIFIED, "binding"),
        ("new", "logged_out", AccountStatus.VERIFIED, "passive_cookie"),
        ("new", "logged_in", AccountStatus.NEEDS_LOGIN, "post_restart_probe"),
    ],
)
def test_failed_restart_stages_never_grant_persistence(
    after,
    cookie_status,
    post_status,
    expected_stage,
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "chrome-profile"
    state_path = tmp_path / "setup-state.json"
    before = _binding(profile, 101)
    after_binding = _binding(profile, 202) if after == "new" else None
    bindings = iter([before, after_binding])
    probes = iter([
        AccountState(status=AccountStatus.VERIFIED),
        AccountState(status=post_status),
    ])
    monkeypatch.setattr(
        "backend.application.setup.login_persistence.chrome_profile_dir",
        lambda: profile,
    )

    result = verify_login_persistence(
        state_path,
        "x",
        probe=lambda _platform: next(probes),
        restart=lambda: {"ok": True},
        cookie_check=lambda *_args: {"status": cookie_status},
        read_binding=lambda: next(bindings),
        profile_write_lock=nullcontext,
        browser_lock=nullcontext,
    )

    assert result["persisted"] is False
    assert result["persistence_stage"] == expected_stage


def test_same_pid_is_not_a_new_binding(tmp_path, monkeypatch):
    profile = tmp_path / "chrome-profile"
    state_path = tmp_path / "setup-state.json"
    bindings = iter([_binding(profile, 101), _binding(profile, 101)])
    monkeypatch.setattr(
        "backend.application.setup.login_persistence.chrome_profile_dir",
        lambda: profile,
    )

    result = verify_login_persistence(
        state_path,
        "reddit",
        probe=lambda _platform: AccountState(status=AccountStatus.VERIFIED),
        restart=lambda: {"ok": True},
        read_binding=lambda: next(bindings),
        profile_write_lock=nullcontext,
        browser_lock=nullcontext,
    )

    assert result["persisted"] is False
    assert result["persistence_stage"] == "binding"


def test_verification_immediately_revokes_old_persistence_while_probe_is_running(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "chrome-profile"
    state_path = tmp_path / "setup-state.json"
    state = SetupState.from_dict({})
    state.accounts["x"] = AccountState(
        status=AccountStatus.VERIFIED,
        persisted=True,
        persistence_stage="verified",
    )
    save_state(state_path, state)
    probe_started = threading.Event()
    release_probe = threading.Event()
    result: dict = {}
    bindings = iter([_binding(profile, 101), _binding(profile, 202)])
    monkeypatch.setattr(
        "backend.application.setup.login_persistence.chrome_profile_dir",
        lambda: profile,
    )

    def blocking_probe(_platform):
        probe_started.set()
        assert release_probe.wait(timeout=2)
        return AccountState(status=AccountStatus.VERIFIED)

    thread = threading.Thread(
        target=lambda: result.update(verify_login_persistence(
            state_path,
            "x",
            probe=blocking_probe,
            restart=lambda: {"ok": True},
            cookie_check=lambda *_args: {"status": "logged_in"},
            read_binding=lambda: next(bindings),
            profile_write_lock=nullcontext,
            browser_lock=nullcontext,
        )),
    )
    thread.start()
    assert probe_started.wait(timeout=2)

    in_progress = load_state(state_path).accounts["x"]
    assert in_progress.persisted is False
    assert in_progress.persistence_stage == "verifying"

    release_probe.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result["persisted"] is True


def test_concurrent_verifications_persist_in_actual_serial_execution_order(
    tmp_path,
    monkeypatch,
):
    profile = tmp_path / "chrome-profile"
    state_path = tmp_path / "setup-state.json"
    serial_lock = threading.Lock()
    first_probe_started = threading.Event()
    release_first = threading.Event()
    second_lock_attempted = threading.Event()
    results: dict[str, dict] = {}
    monkeypatch.setattr(
        "backend.application.setup.login_persistence.chrome_profile_dir",
        lambda: profile,
    )

    def lock_factory():
        if threading.current_thread().name == "second-verify":
            second_lock_attempted.set()
        return serial_lock

    first_bindings = iter([_binding(profile, 101), _binding(profile, 202)])

    def first_probe(_platform):
        first_probe_started.set()
        assert release_first.wait(timeout=2)
        return AccountState(status=AccountStatus.VERIFIED)

    first = threading.Thread(
        name="first-verify",
        target=lambda: results.setdefault("first", verify_login_persistence(
            state_path,
            "x",
            probe=first_probe,
            restart=lambda: {"ok": True},
            cookie_check=lambda *_args: {"status": "logged_in"},
            read_binding=lambda: next(first_bindings),
            profile_write_lock=lock_factory,
            browser_lock=nullcontext,
        )),
    )
    second = threading.Thread(
        name="second-verify",
        target=lambda: results.setdefault("second", verify_login_persistence(
            state_path,
            "x",
            probe=lambda _platform: AccountState(status=AccountStatus.VERIFIED),
            restart=lambda: {"ok": False},
            read_binding=lambda: _binding(profile, 202),
            profile_write_lock=lock_factory,
            browser_lock=nullcontext,
        )),
    )

    first.start()
    assert first_probe_started.wait(timeout=2)
    second.start()
    assert second_lock_attempted.wait(timeout=2)
    time.sleep(0.05)
    assert load_state(state_path).accounts["x"].persistence_stage == "verifying"
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive() and not second.is_alive()
    assert results["first"]["persisted"] is True
    assert results["second"]["persisted"] is False
    final = load_state(state_path).accounts["x"]
    assert final.persisted is False
    assert final.persistence_stage == "restart"


def test_lock_timeout_does_not_overwrite_a_completed_verification(tmp_path):
    state_path = tmp_path / "setup-state.json"
    state = SetupState.from_dict({})
    state.accounts["x"] = AccountState(
        status=AccountStatus.VERIFIED,
        persisted=True,
        persistence_stage="verified",
    )
    save_state(state_path, state)

    def timed_out_lock():
        raise TimeoutError("busy")

    result = verify_login_persistence(
        state_path,
        "x",
        profile_write_lock=timed_out_lock,
        browser_lock=nullcontext,
    )

    assert result["persisted"] is False
    assert result["persistence_stage"] == "lock"
    stored = load_state(state_path).accounts["x"]
    assert stored.persisted is True
    assert stored.persistence_stage == "verified"
