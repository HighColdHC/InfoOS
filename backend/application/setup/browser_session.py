"""Application actions for the dedicated browser session UI."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

import uuid
from pathlib import Path

from backend.infrastructure.browser import session_state
from backend.infrastructure.browser.cookie_status import (
    authorization_url_for_platform,
    check_cookie_login,
    login_url_for_platform,
)
from backend.infrastructure.runtime.environment import browser_console_url, chrome_profile_dir
from backend.application.setup.launch import launch_infoos_chrome
from backend.domain.sources.registry import source_browser_platforms
from backend.infrastructure.runtime.environment import setup_state_path
from backend.infrastructure.runtime.setup_state import (
    AccountState,
    AccountStatus,
    update_account_state,
)


_AUTHORIZATION_ONLY_PLATFORMS = frozenset()


def read_browser_session() -> dict:
    state = session_state.read()
    if not session_state.is_alive(state):
        return {"status": "not_started"}
    return {
        "status": "ready", "context_id": state.context_id, "alias": state.alias,
        "profile_dir": state.profile_dir, "started_at": state.started_at,
        "last_health_at": state.last_health_at, "last_health_status": state.last_health_status,
    }


def launch_browser_session() -> dict[str, str | bool]:
    result = launch_infoos_chrome()
    if not result.get("ok"):
        return {"ok": False, "error": str(result.get("error") or "专用浏览器启动失败")}
    return {
        "ok": True,
        "task_id": str(uuid.uuid4()),
        "console_url": browser_console_url(),
    }


def check_platform_login(platform: str, *, state_path: Path | None = None) -> dict:
    _require_login_platform(platform)
    if platform in _AUTHORIZATION_ONLY_PLATFORMS:
        return _check_authorization_only_login(platform, state_path=state_path)
    state = session_state.read()
    # Login cookies and the Browser Bridge binding are separate facts. An
    # explicit status check may read the configured dedicated profile even
    # before browser-session.json exists; it remains local and passive.
    profile_dir = state.profile_dir if session_state.is_alive(state) and state else chrome_profile_dir()
    result = check_cookie_login(platform, profile_dir)
    account_status = _persisted_account_status(result["status"])
    checked_at = utc_now_micro_z()
    target_path = state_path or setup_state_path()
    update_account_state(
        target_path,
        platform,
        lambda previous: _passive_account_update(
            previous,
            status=account_status,
            checked_at=checked_at,
            error=str(result["error"]) if result.get("error") else None,
            cookie_status=result["status"],
        ),
    )
    return {**result, "ok": result["status"] == "logged_in", "last_checked": checked_at}


def launch_platform_login(platform: str) -> dict:
    _require_login_platform(platform)
    authorization_url = authorization_url_for_platform(platform)
    if authorization_url is None:
        raise ValueError(f"unsupported login platform: {platform}")
    result = launch_infoos_chrome(start_url=authorization_url)
    if not result.get("ok"):
        return {"ok": False, "platform": platform, "error": result.get("error", "专用浏览器启动失败")}
    return {
        "ok": True,
        "platform": platform,
        "start_url": authorization_url,
        "console_url": browser_console_url(),
    }


def _require_login_platform(platform: str) -> None:
    supported = platform in source_browser_platforms() or platform in _AUTHORIZATION_ONLY_PLATFORMS
    if not supported or login_url_for_platform(platform) is None:
        raise ValueError(f"unsupported login platform: {platform}")


def _check_authorization_only_login(platform: str, *, state_path: Path | None) -> dict:
    from backend.application.setup.verify import probe_platform

    account = probe_platform(platform)
    checked_at = account.last_checked or utc_now_micro_z()
    target_path = state_path or setup_state_path()
    update_account_state(
        target_path,
        platform,
        lambda previous: AccountState(
            status=account.status,
            last_checked=checked_at,
            error=account.error,
            persisted=previous.persisted,
            persistence_checked_at=previous.persistence_checked_at,
            persistence_stage=previous.persistence_stage,
            persistence_error=previous.persistence_error,
            raw=dict(previous.raw),
        ),
    )
    status = "logged_in" if account.status == AccountStatus.VERIFIED else (
        "logged_out" if account.status == AccountStatus.NEEDS_LOGIN else "failed"
    )
    return {
        "platform": platform,
        "checked": True,
        "status": status,
        "error": account.error,
        "login_url": login_url_for_platform(platform),
        "method": "opencli",
        "ok": account.status == AccountStatus.VERIFIED,
        "last_checked": checked_at,
    }


def _persisted_account_status(status: str) -> AccountStatus:
    if status == "logged_in":
        return AccountStatus.VERIFIED
    if status == "logged_out":
        return AccountStatus.NEEDS_LOGIN
    if status == "not_configured":
        return AccountStatus.UNKNOWN
    return AccountStatus.FAILED


def _passive_account_update(
    previous: AccountState,
    *,
    status: AccountStatus,
    checked_at: str,
    error: str | None,
    cookie_status: str,
) -> AccountState:
    persisted = previous.persisted
    persistence_checked_at = previous.persistence_checked_at
    persistence_stage = previous.persistence_stage
    persistence_error = previous.persistence_error
    if cookie_status == "logged_out":
        persisted = False
        persistence_checked_at = checked_at
        persistence_stage = "passive_logged_out"
        persistence_error = "未在专用浏览器中找到该平台的登录凭据"
    return AccountState(
        status=status,
        last_checked=checked_at,
        error=error,
        persisted=persisted,
        persistence_checked_at=persistence_checked_at,
        persistence_stage=persistence_stage,
        persistence_error=persistence_error,
        raw=dict(previous.raw),
    )
