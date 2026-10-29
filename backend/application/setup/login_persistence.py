"""Synchronous login-persistence verification for source browser platforms."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from pathlib import Path
from typing import Any, Callable, ContextManager

from backend.application.setup.verify import probe_platform
from backend.domain.sources.registry import source_browser_platforms
from backend.infrastructure.browser import session_state
from backend.infrastructure.browser.cookie_status import check_cookie_login
from backend.infrastructure.browser.setup_launcher import restart_infoos_chrome_for_persistence
from backend.infrastructure.opencli.runtime import opencli_browser_lock
from backend.infrastructure.opencli.tab_lease import opencli_profile_write_lock
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.infrastructure.runtime.setup_state import (
    AccountState,
    AccountStatus,
    update_account_state,
)

Probe = Callable[[str], AccountState]
Restart = Callable[[], dict[str, Any]]
CookieCheck = Callable[[str, str | Path | None], dict[str, Any]]
BindingRead = Callable[[], session_state.BrowserSessionState | None]
LockFactory = Callable[[], ContextManager[None]]


def verify_login_persistence(
    state_path: Path,
    platform: str,
    *,
    probe: Probe = probe_platform,
    restart: Restart = restart_infoos_chrome_for_persistence,
    cookie_check: CookieCheck = check_cookie_login,
    read_binding: BindingRead = session_state.read_fresh,
    profile_write_lock: LockFactory = opencli_profile_write_lock,
    browser_lock: LockFactory = opencli_browser_lock,
) -> dict[str, Any]:
    """Prove functional login before and after a graceful profile restart."""
    checked_at = utc_now_micro_z()
    if platform not in source_browser_platforms():
        return _response(
            platform=platform,
            account=AccountState(
                status=AccountStatus.FAILED,
                last_checked=checked_at,
                persisted=False,
                persistence_checked_at=checked_at,
                persistence_stage="platform",
                persistence_error="Unsupported browser platform",
            ),
        )

    try:
        with profile_write_lock():
            with browser_lock():
                _persist_account(state_path, platform, lambda previous: _verifying(previous, checked_at))
                try:
                    account = _run_verification(
                        platform,
                        checked_at=checked_at,
                        probe=probe,
                        restart=restart,
                        cookie_check=cookie_check,
                        read_binding=read_binding,
                    )
                except Exception:  # noqa: BLE001 - persist a safe failure before releasing the locks
                    account = _failure(checked_at, "internal", "Login persistence verification failed")
                stored = _persist_account(
                    state_path,
                    platform,
                    lambda previous: _preserve_unknown_fields(previous, account),
                )
    except TimeoutError:
        account = _failure(checked_at, "lock", "Browser verification is busy; try again")
        stored = account
    except Exception:  # noqa: BLE001 - lock entry/exit and state I/O use the safe failure contract
        account = _failure(checked_at, "internal", "Login persistence verification failed")
        stored = account
    return _response(platform=platform, account=stored)


def _run_verification(
    platform: str,
    *,
    checked_at: str,
    probe: Probe,
    restart: Restart,
    cookie_check: CookieCheck,
    read_binding: BindingRead,
) -> AccountState:
    profile = chrome_profile_dir()
    before = read_binding()
    if not session_state.is_proven_dedicated_binding(before, profile_dir=profile):
        return _failure(checked_at, "precondition", "Dedicated Chrome binding is not verified")
    pre_probe = probe(platform)
    if pre_probe.status != AccountStatus.VERIFIED:
        return _failure(
            checked_at,
            "pre_restart_probe",
            "Functional login probe was not verified before restart",
            status=pre_probe.status,
        )
    restarted = restart()
    if not restarted.get("ok"):
        return _failure(checked_at, "restart", "Dedicated Chrome did not restart gracefully")
    after = read_binding()
    if not _is_new_binding(before, after, profile):
        return _failure(checked_at, "binding", "Restarted Chrome binding proof is invalid")
    cookie = cookie_check(platform, profile)
    if cookie.get("status") != "logged_in":
        status = AccountStatus.NEEDS_LOGIN if cookie.get("status") == "logged_out" else AccountStatus.FAILED
        return _failure(
            checked_at,
            "passive_cookie",
            "Passive login state was not logged in after restart",
            status=status,
        )
    post_probe = probe(platform)
    if post_probe.status != AccountStatus.VERIFIED:
        return _failure(
            checked_at,
            "post_restart_probe",
            "Functional login probe was not verified after restart",
            status=post_probe.status,
        )
    return AccountState(
        status=AccountStatus.VERIFIED,
        last_checked=post_probe.last_checked or checked_at,
        error=None,
        persisted=True,
        persistence_checked_at=checked_at,
        persistence_stage="verified",
        persistence_error=None,
    )


def _is_new_binding(
    before: session_state.BrowserSessionState,
    after: session_state.BrowserSessionState | None,
    profile: Path,
) -> bool:
    return (
        session_state.is_proven_dedicated_binding(after, profile_dir=profile)
        and after is not None
        and after.profile_dir == before.profile_dir
        and after.chrome_pid != before.chrome_pid
    )


def _failure(
    checked_at: str,
    stage: str,
    error: str,
    *,
    status: AccountStatus = AccountStatus.FAILED,
) -> AccountState:
    return AccountState(
        status=status,
        last_checked=checked_at,
        error=error,
        persisted=False,
        persistence_checked_at=checked_at,
        persistence_stage=stage,
        persistence_error=error,
    )


def _verifying(previous: AccountState, checked_at: str) -> AccountState:
    return AccountState(
        status=previous.status,
        last_checked=checked_at,
        error=None,
        persisted=False,
        persistence_checked_at=checked_at,
        persistence_stage="verifying",
        persistence_error=None,
        raw=dict(previous.raw),
    )


def _persist_account(
    state_path: Path,
    platform: str,
    update: Callable[[AccountState], AccountState],
) -> AccountState:
    return update_account_state(state_path, platform, update)


def _preserve_unknown_fields(previous: AccountState, account: AccountState) -> AccountState:
    account.raw = dict(previous.raw)
    return account


def _response(*, platform: str, account: AccountState) -> dict[str, Any]:
    return {
        "ok": account.persisted,
        "platform": platform,
        "status": account.status.value,
        "last_checked": account.last_checked,
        "error": account.error,
        "persisted": account.persisted,
        "persistence_checked_at": account.persistence_checked_at,
        "persistence_stage": account.persistence_stage,
    }
