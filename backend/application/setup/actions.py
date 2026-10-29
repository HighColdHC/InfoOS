"""Setup wizard application actions and state transitions."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from pathlib import Path
from typing import Any, Callable

from backend.application.setup.state import (
    AccountState,
    AccountStatus,
    SetupState,
    load_state,
    save_state,
)
from backend.application.setup.login_persistence import verify_login_persistence
from backend.infrastructure.browser import opencli_health, session_state
from backend.infrastructure.browser.setup_discovery import inspect_opencli_extension
from backend.infrastructure.browser.setup_launcher import bind_infoos_chrome
from backend.infrastructure.opencli.commands import ensure_daemon as ensure_opencli_daemon
from backend.infrastructure.runtime.environment import chrome_profile_dir
from backend.infrastructure.runtime.setup_state import update_account_state

INFOOS_OPENCLI_ALIAS = "infoos-fetch"


def read_status(state_path: Path) -> dict[str, Any]:
    return load_state(state_path).to_dict()


def reset_setup(state_path: Path, complete_flag: Path) -> dict[str, Any]:
    state = SetupState.from_dict({})
    save_state(state_path, state)
    try:
        complete_flag.unlink()
    except OSError:
        pass
    return state.to_dict()


def dismiss_setup(state_path: Path) -> dict[str, Any]:
    state = load_state(state_path)
    state.current_step = "skipped"
    save_state(state_path, state)
    return state.to_dict()


def skip_platform(state_path: Path, platform: str) -> dict[str, str]:
    update_account_state(
        state_path,
        platform,
        lambda previous: AccountState(
            status=AccountStatus.SKIPPED,
            last_checked=utc_now_micro_z(),
            raw=dict(previous.raw),
        ),
    )
    return {"status": "skipped"}


def verify_platform_login(
    state_path: Path,
    platform: str,
) -> dict[str, Any]:
    return verify_login_persistence(state_path, platform)


def verify_extension_binding(
    state_path: Path,
    *,
    inspect_extension: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inspect the bridge and ask the dedicated launcher to prove its binding."""
    state = load_state(state_path)
    info = (inspect_extension or inspect_opencli_extension)()
    state.extension.loaded = bool(info["loaded"])
    state.extension.id = info.get("id")
    state.extension.manifest_version = info.get("manifest_version")
    if not state.extension.loaded:
        return _save_failure(state_path, state, "no-extension", "Install the OpenCLI Browser Bridge extension in InfoOS Chrome, then click Verify again.")
    state.extension.verified_at = utc_now_micro_z()
    if "install_extension" not in state.completed_steps:
        state.completed_steps.append("install_extension")
    try:
        snapshot = opencli_health.refresh()
    except Exception as exc:  # noqa: BLE001 - OpenCLI health subprocess failures must return an explicit setup verification failure
        return _save_failure(state_path, state, "opencli-missing", f"Could not reach opencli daemon: {exc}")
    if not snapshot.get("daemon_running"):
        started = ensure_opencli_daemon()
        if started.returncode == 0:
            try:
                snapshot = opencli_health.refresh()
            except Exception as exc:  # noqa: BLE001 - OpenCLI health subprocess failures after daemon startup must return a setup verification failure
                return _save_failure(
                    state_path,
                    state,
                    "opencli-missing",
                    f"Could not reach OpenCLI daemon after startup: {exc}",
                )
    if not snapshot.get("daemon_running"):
        return _save_failure(state_path, state, "opencli-missing", snapshot.get("hint") or "opencli daemon is not running. Install or start it, then try again.")
    bound = bind_infoos_chrome()
    if not bound.get("ok"):
        return _save_failure(
            state_path,
            state,
            "binding-failed",
            bound.get("error") or "Could not establish a proven dedicated Chrome binding.",
        )
    binding = session_state.read_fresh()
    if not session_state.is_proven_dedicated_binding(
        binding,
        profile_dir=chrome_profile_dir(),
    ):
        return _save_failure(
            state_path,
            state,
            "binding-persistence-failed",
            "The dedicated launcher returned without a valid schema-v2 binding proof. Verify the Chrome profile and try again.",
        )
    bound_at = binding.started_at
    state.session.context_id = binding.context_id
    state.session.alias = INFOOS_OPENCLI_ALIAS
    state.session.bound_at = bound_at
    if "bind_profile" not in state.completed_steps:
        state.completed_steps.append("bind_profile")
    state.current_step = "login_accounts"
    save_state(state_path, state)
    from backend.application.setup.environment_snapshot import refresh_component_snapshot

    refresh_component_snapshot(state_path)
    return {
        "ok": True,
        "status": "bound",
        "current_step": state.current_step,
        "extension": state.to_dict()["extension"],
        "context_id": binding.context_id,
        "connected_profiles": [binding.context_id],
        "hint": "",
    }


def _save_failure(state_path: Path, state: SetupState, status: str, hint: str) -> dict[str, Any]:
    save_state(state_path, state)
    return {"ok": False, "status": status, "current_step": state.current_step, "extension": state.to_dict()["extension"], "hint": hint}
