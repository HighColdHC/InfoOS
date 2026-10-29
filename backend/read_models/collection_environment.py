"""Purely passive projection for the collection-environment page.

This module reads persisted setup/session/source facts only.  Installation,
version, browser, OpenCLI and cookie probes belong to explicit application
actions and must never be added here.
"""
from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict

from backend.domain.calibration.matrix import REQUIRED_LOGIN
from backend.domain.sources.capability import resolve_source_capability
from backend.domain.sources.registry import PLATFORM_REGISTRY, source_browser_platforms, browser_platforms
from backend.domain.tasks import fetch_circuit_source_id
from backend.infrastructure.browser import session_state
from backend.infrastructure.runtime.container_health import ContainerHealth, read_container_health
from backend.infrastructure.runtime.setup_state import AccountState, AccountStatus, COMPONENT_IDS, load_state
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.auth_recovery import AuthRecoveryRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.runtime_circuits_types import SafeCircuitRow


class RuntimeCircuitItem(SafeCircuitRow):
    source_id: NotRequired[str]
    source_name: NotRequired[str]


class AuthRecoveryItem(TypedDict):
    incident_id: str
    platform: str
    status: str
    reason_code: str
    affected_task_count: int
    created_at: str
    updated_at: str
    last_error_code: str | None
    open_console_url: str
    verify_url: str


class ComponentItem(TypedDict):
    id: str
    name: str
    status: str
    version: str | None
    is_chrome: bool
    action: str | None


class AccountItem(TypedDict):
    platform: str
    label: str
    status: str
    browser_capability: str
    session_id: str | None
    last_checked_at: str | None
    affected_source_count: int
    action_hint: str | None
    error: str | None


class CollectionEnvironment(TypedDict):
    runtime_health: ContainerHealth
    runtime_circuits: list[RuntimeCircuitItem]
    pending_auth_recoveries: list[AuthRecoveryItem]
    components: list[ComponentItem]
    accounts: list[AccountItem]
    snapshot_at: str | None


COMPONENT_NAMES = {
    "dedicated_chrome": "专用 Chrome",
    "opencli_bridge": "OpenCLI Bridge",
    "yt_dlp": "yt-dlp",
    "gallery_dl": "gallery-dl",
    "ffmpeg": "FFmpeg",
    "local_asr": "本地 ASR",
    "sqlite": "SQLite",
}


AUTHORIZATION_ONLY_ACCOUNTS: dict[str, str] = {}


def read_collection_environment(
    base_dir: Path | str,
    *,
    state_path: Path,
) -> CollectionEnvironment:
    setup = load_state(state_path)
    browser_session = session_state.read()
    session_id = (
        browser_session.context_id
        if session_state.is_alive(browser_session) and browser_session is not None
        else None
    )
    affected_counts = _affected_source_counts(base_dir)
    return {
        "runtime_health": read_container_health(),
        "runtime_circuits": _runtime_circuits(base_dir),
        "pending_auth_recoveries": _pending_auth_recoveries(base_dir),
        "components": [
            {
                "id": component_id,
                "name": COMPONENT_NAMES[component_id],
                "status": setup.components[component_id].status,
                "version": setup.components[component_id].version,
                "is_chrome": component_id == "dedicated_chrome",
                "action": "launch" if component_id == "dedicated_chrome" else None,
            }
            for component_id in COMPONENT_IDS
        ],
        "accounts": [
            _account_item(
                platform,
                setup.accounts[platform],
                session_id=session_id,
                affected_source_count=affected_counts.get(platform, 0),
            )
            for platform in _account_platforms()
        ],
        "snapshot_at": setup.component_snapshot_at,
    }


def _runtime_circuits(base_dir: Path | str) -> list[RuntimeCircuitItem]:
    circuits: list[RuntimeCircuitItem] = [{**row} for row in RuntimeCircuitRepository(base_dir).list_safe()]
    sources = SourceRepository(base_dir)
    for circuit in circuits:
        source_id = fetch_circuit_source_id(circuit["scope_key"]) if circuit["scope_kind"] == "platform" else None
        if source_id is not None:
            source = sources.get_source(source_id)
            circuit["source_id"] = source_id
            circuit["source_name"] = (source["display_name"] if source else None) or source_id
    return circuits


def _pending_auth_recoveries(base_dir: Path | str) -> list[AuthRecoveryItem]:
    incidents = AuthRecoveryRepository(base_dir).list_actionable()
    return [
        {
            "incident_id": incident["incident_id"],
            "platform": incident["platform"],
            "status": incident["status"],
            "reason_code": incident["reason_code"],
            "affected_task_count": incident["affected_task_count"],
            "created_at": incident["created_at"],
            "updated_at": incident["updated_at"],
            "last_error_code": incident["last_error_code"],
            "open_console_url": (
                f"/api/auth-recoveries/{incident['incident_id']}/open-console"
            ),
            "verify_url": f"/api/auth-recoveries/{incident['incident_id']}/verify",
        }
        for incident in incidents
        if incident["platform"] in browser_platforms()
    ]


def _account_platforms() -> tuple[str, ...]:
    supported = source_browser_platforms()
    source_accounts = tuple(platform for platform in PLATFORM_REGISTRY if platform in supported)
    return (*source_accounts, *AUTHORIZATION_ONLY_ACCOUNTS)


def _affected_source_counts(base_dir: Path | str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in SourceRepository(base_dir).list_sources(include_disabled=False):
        platform = source["platform"].strip().lower()
        if (
            platform in source_browser_platforms()
            and resolve_source_capability(source).requires_login == REQUIRED_LOGIN
        ):
            counts[platform] = counts.get(platform, 0) + 1
    return counts


def _account_item(
    platform: str,
    account: AccountState,
    *,
    session_id: str | None,
    affected_source_count: int,
) -> AccountItem:
    status = _account_status(account.status)
    return {
        "platform": platform,
        "label": (
            PLATFORM_REGISTRY[platform].label
            if platform in PLATFORM_REGISTRY
            else AUTHORIZATION_ONLY_ACCOUNTS[platform]
        ),
        "status": status,
        "browser_capability": "dedicated_chrome",
        "session_id": session_id,
        "last_checked_at": account.last_checked,
        "affected_source_count": affected_source_count,
        "action_hint": _action_hint(
            status,
            affected_source_count,
            session_bound=session_id is not None,
        ),
        "error": account.error,
    }


def _account_status(status: AccountStatus) -> str:
    if status == AccountStatus.VERIFIED:
        return "verified"
    if status == AccountStatus.NEEDS_LOGIN:
        return "needs_login"
    if status == AccountStatus.FAILED:
        return "failed"
    return "unknown"


def _action_hint(
    status: str,
    affected_source_count: int,
    *,
    session_bound: bool,
) -> str | None:
    if status == "verified" and not session_bound:
        return "已登录；请运行首次设置绑定 OpenCLI Bridge"
    if status in {"needs_login", "failed"}:
        if affected_source_count:
            return f"请在专用 Chrome 中登录；当前影响 {affected_source_count} 个来源"
        return "请在专用 Chrome 中登录"
    if status == "unknown":
        return "尚未检查，请执行状态检查"
    return None
