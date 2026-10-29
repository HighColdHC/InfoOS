"""Browser-session profile binding helpers.

Split from infoos.cli.browser_session (PR 2i of the backend restructure;
the infoos.cli/ shim was removed in PR 6).

Owns the chain of profile-state operations:
  - _profile_payload: shape the ``profile`` block of the result dict.
  - _expected_profile_dir / _read_valid_session_state: read on-disk state.
  - _refresh_existing_profile_binding: passive check (Chrome running, state
    file present, session bound to a connected OpenCLI profile).
  - _session_binding_connected / _connected_opencli_profile_names /
    _parse_profile_list: ask OpenCLI which Browser Bridge profiles are
    currently connected.
  - _ensure_profile_binding: end-to-end binding — reuse existing if alive,
    else promote a freshly-seen profile by renaming + using it.
  - _rename_and_use_opencli_profile / _write_session_binding: write side
    of the binding.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

import os
import re
import subprocess
from pathlib import Path

from backend.infrastructure.browser import session_state
from backend.infrastructure.browser.session_state import BrowserSessionState
from backend.infrastructure.opencli.runtime import resolve_opencli_executable
from backend.application.usecases.opencli_status import (
    _infoos_chrome_profile_pids,
    _infoos_chrome_profile_running,
)
from backend.application.usecases.source_config import DEFAULT_INFOOS_CHROME_PROFILE_DIR

_INFOOS_OPENCLI_ALIAS = "infoos-fetch"
_PROFILE_LIST_TIMEOUT_SECONDS = 10


def _profile_payload(
    *,
    running: bool,
    state: BrowserSessionState | None = None,
    binding_status: str = "missing",
    binding_error: str | None = None,
) -> dict:
    payload = {
        "running": running,
        "required": str(DEFAULT_INFOOS_CHROME_PROFILE_DIR),
        "bound": False,
        "binding_status": binding_status,
        "binding_error": binding_error,
        "alias": None,
        "context_id": None,
        "profile_dir": None,
    }
    if state is not None:
        payload.update(
            {
                "bound": session_state.is_alive(state),
                "alias": state.alias,
                "context_id": state.context_id,
                "profile_dir": state.profile_dir,
            }
        )
    return payload


def _expected_profile_dir() -> Path:
    return Path(DEFAULT_INFOOS_CHROME_PROFILE_DIR).expanduser()


def _read_valid_session_state() -> BrowserSessionState | None:
    state = session_state.read()
    if not session_state.is_alive(state) or not isinstance(state, BrowserSessionState):
        return None
    try:
        if Path(state.profile_dir).expanduser() != _expected_profile_dir():
            return None
    except TypeError:
        return None
    return state


def _refresh_existing_profile_binding(result: dict, executable: str) -> bool:
    running = _infoos_chrome_profile_running()
    state = _read_valid_session_state()
    if not running:
        result["profile"] = _profile_payload(running=False, state=state, binding_status="not_running")
        return False
    if state is None:
        result["profile"] = _profile_payload(running=True, binding_status="missing")
        return False
    if (
        os.environ.get("INFOOS_BROWSER_RUNTIME", "").strip()
        == "supervised-container"
        and state.chrome_pid not in _infoos_chrome_profile_pids()
    ):
        result["profile"] = _profile_payload(
            running=True,
            state=state,
            binding_status="stale",
            binding_error="已记录的 Chrome 进程与专用配置不匹配",
        )
        return False
    if _session_binding_connected(executable, state):
        if not _use_opencli_profile(executable, state.context_id, result):
            return False
        result["profile"] = _profile_payload(running=True, state=state, binding_status="ready")
        result["profile"]["bound"] = True
        return True
    result["profile"] = _profile_payload(
        running=True,
        state=state,
        binding_status="stale",
        binding_error="专用 OpenCLI 配置未连接",
    )
    return False


def _session_binding_connected(executable: str, state: BrowserSessionState) -> bool:
    names, _ = _connected_opencli_profile_names(executable)
    return any(name in {state.alias, state.context_id} for name in names)


def _connected_opencli_profile_names(executable: str) -> tuple[list[str], str | None]:
    resolved_executable = resolve_opencli_executable(executable)
    try:
        profile_list = subprocess.run(
            [resolved_executable, "profile", "list"],
            capture_output=True,
            text=True,
            timeout=_PROFILE_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return [], "无法读取专用 OpenCLI 配置状态"
    except subprocess.TimeoutExpired:
        return [], "无法读取专用 OpenCLI 配置状态"
    output = "\n".join(part for part in [profile_list.stdout, profile_list.stderr] if part)
    if profile_list.returncode != 0:
        return [], "无法读取专用 OpenCLI 配置列表"
    names: list[str] = []
    for profile in _parse_profile_list(output):
        if profile["status"] == "connected" and profile["name"] not in names:
            names.append(profile["name"])
    return names, None


def _parse_profile_list(output: str) -> list[dict[str, str]]:
    """Parse `opencli profile list` output.

    opencli 1.8.0 emits::

        "  <contextId> [<alias> [default]] — <status> [v<version>]"

    Earlier opencli versions only had a single token before the
    em-dash::

        "  <name> — <status> v<version>"

    The first token is always the contextId (or, in the old format,
    the only token). Returned as ``name``. The caller
    ``_session_binding_connected`` matches this against
    ``state.context_id`` and ``state.alias``.
    """
    profiles: list[dict[str, str]] = []
    for line in output.splitlines():
        text = line.strip().lstrip("*").strip()
        if not text or text.lower().startswith((
            "connected browser bridge profiles",
            "update available",
            "run:",
            "download:",
            "open a chrome",
        )):
            continue
        match = re.match(
            r"^\s*(?P<name>\S+)"
            r"(?:\s+(?P<alias>\S+))?"
            r"(?:\s+(?P<default>default))?"
            r"\s*[—\-:]\s*"
            r"(?P<status>\w+)"
            r"(?:\s+v?(?P<version>[\d.]+))?",
            line,
        )
        if match:
            profiles.append({"name": match.group("name"), "status": match.group("status").lower()})
    return profiles


def _ensure_profile_binding(result: dict, executable: str, *, previous_connected_profiles: list[str]) -> bool:
    if _refresh_existing_profile_binding(result, executable):
        return True
    if not result["profile"]["running"]:
        return False

    current_profiles, error = _connected_opencli_profile_names(executable)
    if error is not None:
        result["profile"]["binding_error"] = error
        return False
    previous = set(previous_connected_profiles)
    candidates = [name for name in current_profiles if name not in previous and name != _INFOOS_OPENCLI_ALIAS]
    if _INFOOS_OPENCLI_ALIAS in current_profiles and _INFOOS_OPENCLI_ALIAS not in previous:
        state = _write_session_binding(context_id=_INFOOS_OPENCLI_ALIAS)
        result["profile"] = _profile_payload(running=True, state=state, binding_status="ready")
        result["profile"]["bound"] = True
        return True
    if len(candidates) != 1:
        result["profile"]["binding_status"] = "ambiguous" if candidates else "missing"
        result["profile"]["binding_error"] = (
            "无法确定专用 OpenCLI 会话；请关闭其它 Browser Bridge 会话后再试"
            if candidates
            else "启动专用 Chrome 后未找到新的 OpenCLI 会话"
        )
        return False
    context_id = candidates[0]
    if not _rename_and_use_opencli_profile(executable, context_id, result):
        return False
    state = _write_session_binding(context_id=context_id)
    result["profile"] = _profile_payload(running=True, state=state, binding_status="ready")
    result["profile"]["bound"] = True
    return True


def _rename_and_use_opencli_profile(executable: str, context_id: str, result: dict) -> bool:
    resolved_executable = resolve_opencli_executable(executable)
    commands = []
    if context_id != _INFOOS_OPENCLI_ALIAS:
        commands.append([resolved_executable, "profile", "rename", context_id, _INFOOS_OPENCLI_ALIAS])
    commands.append([resolved_executable, "profile", "use", context_id])
    for command in commands:
        if not _run_profile_command(command, result):
            return False
    return True


def _use_opencli_profile(executable: str, context_id: str, result: dict) -> bool:
    resolved_executable = resolve_opencli_executable(executable)
    return _run_profile_command([resolved_executable, "profile", "use", context_id], result)


def _run_profile_command(command: list[str], result: dict) -> bool:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_PROFILE_LIST_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        result["profile"]["binding_error"] = "无法操作专用 OpenCLI 配置"
        return False
    except subprocess.TimeoutExpired:
        result["profile"]["binding_error"] = "无法操作专用 OpenCLI 配置"
        return False
    if completed.returncode != 0:
        result["profile"]["binding_error"] = (
            "专用 OpenCLI 配置操作失败"
        )
        return False
    return True


def _write_session_binding(*, context_id: str) -> BrowserSessionState:
    now = utc_now_micro_z()
    state = BrowserSessionState(
        schema_version=1,
        profile_dir=str(_expected_profile_dir()),
        context_id=context_id,
        alias=_INFOOS_OPENCLI_ALIAS,
        started_at=now,
        last_health_at=now,
        last_health_status="ready",
    )
    session_state.write(state)
    return state
