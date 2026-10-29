"""Per-platform login verification probes.

The wizard calls ``probe_platform(platform)`` after the user signs in.
Each platform's probe runs a small opencli command and inspects the
result. The probe is intentionally **decoupled** from
``prepare_opencli_browser_session``: preparing a runtime fetch
environment is the scheduler's job, not the wizard's. The wizard
first requires the dedicated InfoOS browser-session binding, then asks
OpenCLI "is this platform currently logged in?" and records the answer.

- X / YouTube: ``opencli <platform> whoami``. Public reads are not
  login evidence; the probe must return an authenticated identity.
- Reddit: ``opencli reddit whoami``. A zero exit code means the
  dedicated browser profile has a usable Reddit session.
- Xiaohongshu / Douyin: ``opencli <platform> whoami``. A zero exit code
  with an identity means the dedicated browser profile has a usable session.
- 生财有术: ``opencli zsxq whoami``. A zero exit code with an identity
  means the dedicated browser profile has a usable ZSXQ session.

Each call returns a small ``AccountState`` dataclass; the wizard
writes the result into setup-state.json.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

import json
import re
from pathlib import Path

from backend.infrastructure.browser import session_state
from backend.infrastructure.opencli.commands import OpenCLIResult, pin_adapter_lifecycle, run_opencli
from backend.infrastructure.runtime.environment import chrome_profile_dir

from backend.application.setup.state import AccountState, AccountStatus

_PROBE_TIMEOUT_SECONDS = 25
# OpenCLI 1.8.0 reports a login failure with rc=69 and a YAML error
# envelope on stderr. These markers cover both the YAML envelope and
# the older JSON-style AUTH_REQUIRED errors.
_NEEDS_LOGIN_MARKERS = (
    "auth_required",
    "not logged in",
    "no ct0 cookie",
    "browser profile",
    "is not connected",
    "browser_connect",
)
_STALE_PAGE_MARKERS = (
    "stale page identity",
    "page not found:",
    "no tab with id:",
)
# Identity keys we look for in either a JSON envelope or a YAML key:value line.
_X_IDENTITY_KEYS = ("screen_name", "username", "id", "user_id", "rest_id")


def _parse_x_profile_payload(stdout: str, stderr: str) -> tuple[dict | None, str | None]:
    """Best-effort parse of `twitter profile` output.

    Returns ``(payload, format)``. ``payload`` is None when nothing
    recognizable was found. ``format`` is "json", "yaml", or None.

    OpenCLI 1.8.0 emits JSON on success but a YAML error envelope on
    failure. Both forms must be accepted. The success path can be a
    JSON *object* (older versions) or a JSON *array* with one
    identity object (1.8.0's ``twitter profile``); both shapes are
    normalized to the inner dict.
    """
    text = (stdout or "").strip()
    if text.startswith("{") or text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, dict):
                return loaded, "json"
            if isinstance(loaded, list) and loaded and isinstance(loaded[0], dict):
                return loaded[0], "json"
        except json.JSONDecodeError:
            pass
    yaml_blob = "\n".join([stdout or "", stderr or ""])
    found: dict[str, str] = {}
    for line in yaml_blob.splitlines():
        m = re.match(r"^\s*([A-Za-z_]\w*)\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip().strip("'\"")
        if key in _X_IDENTITY_KEYS and key not in found:
            found[key] = value
    if found:
        return found, "yaml"
    return None, None


def _run_opencli_command(args: list[str]) -> tuple[int, str, str]:
    """Run an OpenCLI probe through the infrastructure command adapter."""
    session_error = _dedicated_session_error()
    if session_error is not None:
        return 69, "", session_error
    state = session_state.read()
    if isinstance(state, session_state.BrowserSessionState):
        use_profile = _SubprocessResult.from_call(["profile", "use", state.context_id], timeout=10)
        if use_profile.returncode != 0:
            return use_profile
    result = _SubprocessResult.from_call(args, timeout=_PROBE_TIMEOUT_SECONDS)
    if result.returncode != 0 and _looks_like_stale_page_identity(result.stdout, result.stderr):
        result = _SubprocessResult.from_call(args, timeout=_PROBE_TIMEOUT_SECONDS)
    return result


def _looks_like_stale_page_identity(stdout: str, stderr: str) -> bool:
    combined = f"{stdout}\n{stderr}".lower()
    return any(marker in combined for marker in _STALE_PAGE_MARKERS)


def _dedicated_session_error() -> str | None:
    state = session_state.read()
    if not session_state.is_alive(state) or not isinstance(state, session_state.BrowserSessionState):
        return (
            "Dedicated InfoOS browser session is not configured; "
            "Browser profile infoos-fetch is not connected. "
            "Open InfoOS Chrome and verify the OpenCLI extension first."
        )
    try:
        actual_profile = Path(state.profile_dir).expanduser()
    except TypeError:
        return (
            "Dedicated InfoOS browser session is not configured; "
            "Browser profile infoos-fetch is not connected. "
            "Open InfoOS Chrome and verify the OpenCLI extension first."
        )
    if actual_profile != chrome_profile_dir().expanduser():
        return (
            "Dedicated InfoOS browser session points at an unexpected profile; "
            "Browser profile infoos-fetch is not connected. "
            "Open InfoOS Chrome and verify the OpenCLI extension first."
        )
    return None


def _probe_x() -> AccountState:
    rc, stdout, stderr = _run_opencli_command(["twitter", "whoami", "--format", "json"])
    combined = f"{stdout}\n{stderr}".lower()
    payload, _fmt = _parse_x_profile_payload(stdout, stderr)

    if rc != 0:
        if any(marker in combined for marker in _NEEDS_LOGIN_MARKERS):
            return AccountState(
                status=AccountStatus.NEEDS_LOGIN,
                last_checked=utc_now_micro_z(),
                error=(stderr or stdout).strip(),
            )
        return AccountState(
            status=AccountStatus.FAILED,
            last_checked=utc_now_micro_z(),
            error=(stderr or stdout).strip() or f"opencli twitter tweets exited with rc={rc}",
        )

    # rc == 0 — inspect the body. OpenCLI 1.8.0 reports login failure as
    # body {ok:false, error:{...}} even with rc=0 for some commands.
    if isinstance(payload, dict) and payload.get("ok") is False:
        err = payload.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return AccountState(
            status=AccountStatus.NEEDS_LOGIN,
            last_checked=utc_now_micro_z(),
            error=(msg or "").strip() or (stderr or stdout).strip(),
        )

    if isinstance(payload, dict) and any(payload.get(k) for k in _X_IDENTITY_KEYS):
        return AccountState(status=AccountStatus.VERIFIED, last_checked=utc_now_micro_z())

    return AccountState(
        status=AccountStatus.FAILED,
        last_checked=utc_now_micro_z(),
        error=f"X whoami returned no user identity (rc={rc})",
    )


def _probe_youtube() -> AccountState:
    return _probe_whoami("youtube", "YouTube")


def _probe_reddit() -> AccountState:
    return _probe_whoami("reddit", "Reddit")


def _probe_whoami(platform: str, label: str) -> AccountState:
    return _probe_whoami_with_args([platform, "whoami", "--format", "json"], label)


_WHOAMI_IDENTITY_KEYS = {
    "username", "screen_name", "user_id", "id", "rest_id", "channel_id", "email",
}


def _normalize_whoami_payload(payload: object) -> dict | None:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, list):
        return None
    rows = [item for item in payload if isinstance(item, dict)]
    field_values = {
        str(item.get("field") or item.get("key") or "").strip().lower(): item.get("value")
        for item in rows
        if item.get("field") or item.get("key")
    }
    if field_values:
        return field_values
    return rows[0] if len(rows) == 1 else None


def _whoami_identity(payload: object) -> bool:
    normalized = _normalize_whoami_payload(payload)
    if not isinstance(normalized, dict):
        return False
    if any(normalized.get(key) not in (None, "", [], {}) for key in _WHOAMI_IDENTITY_KEYS):
        return True
    user = normalized.get("user")
    return isinstance(user, dict) and any(value not in (None, "", [], {}) for value in user.values())


def _probe_zsxq() -> AccountState:
    args = pin_adapter_lifecycle(["zsxq", "whoami", "--format", "json"])
    return _probe_whoami_with_args(args, "知识星球")


def _probe_whoami_with_args(args: list[str], label: str) -> AccountState:
    rc, stdout, stderr = _run_opencli_command(args)
    combined = (stdout or "").strip() or (stderr or "").strip()
    if rc != 0:
        if any(marker in combined.lower() for marker in _NEEDS_LOGIN_MARKERS):
            return AccountState(status=AccountStatus.NEEDS_LOGIN, last_checked=utc_now_micro_z(), error=combined)
        return AccountState(status=AccountStatus.FAILED, last_checked=utc_now_micro_z(), error=combined)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return AccountState(status=AccountStatus.FAILED, last_checked=utc_now_micro_z(), error=combined)
    normalized = _normalize_whoami_payload(payload)
    if isinstance(normalized, dict) and normalized.get("ok") is False:
        err = normalized.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return AccountState(status=AccountStatus.NEEDS_LOGIN, last_checked=utc_now_micro_z(), error=str(msg) or combined)
    status = str(normalized.get("status") or "").strip().lower() if isinstance(normalized, dict) else ""
    if isinstance(normalized, dict) and (
        normalized.get("logged_in") is False
        or status in {"logged_out", "not_logged_in", "unauthenticated"}
    ):
        return AccountState(status=AccountStatus.NEEDS_LOGIN, last_checked=utc_now_micro_z(), error=combined or f"{label} is not logged in")
    if _whoami_identity(payload):
        return AccountState(status=AccountStatus.VERIFIED, last_checked=utc_now_micro_z())
    if isinstance(normalized, dict) and (
        normalized.get("logged_in") is True or status in {"logged_in", "authenticated"}
    ):
        return AccountState(status=AccountStatus.VERIFIED, last_checked=utc_now_micro_z())
    return AccountState(status=AccountStatus.FAILED, last_checked=utc_now_micro_z(), error=f"{label} whoami returned no identity")


_PROBES = {
    "x": _probe_x,
    "youtube": _probe_youtube,
    "reddit": _probe_reddit,
    "xiaohongshu": lambda: _probe_whoami("xiaohongshu", "Xiaohongshu"),
    "douyin": lambda: _probe_whoami("douyin", "Douyin"),
    "zsxq": _probe_zsxq,
}


class _SubprocessResult(OpenCLIResult):
    """Thin wrapper so ``probe_platform`` can be unit-tested without
    patching the infrastructure adapter. Behaves as a 3-tuple of
    (returncode, stdout, stderr) for compatibility with code that
    unpacks the result."""
    @classmethod
    def from_call(cls, args: list[str], *, timeout: int) -> "_SubprocessResult":
        result = run_opencli(args, timeout=timeout)
        return cls(result.returncode, result.stdout, result.stderr)


def supports_platform_probe(platform: str) -> bool:
    return platform in _PROBES


def probe_platform(platform: str) -> AccountState:
    """Run the platform-specific probe directly against the bound session.

    Returns ``AccountState.status = SKIPPED`` for platforms we do not
    probe yet. On probe error returns FAILED
    with the opencli output as the error message.
    """
    probe = _PROBES.get(platform)
    if probe is None:
        return AccountState(status=AccountStatus.SKIPPED)
    try:
        return probe()
    except Exception as exc:  # noqa: BLE001 — defensive: surface any probe failure to the wizard
        return AccountState(
            status=AccountStatus.FAILED,
            last_checked=utc_now_micro_z(),
            error=f"{type(exc).__name__}: {exc}",
        )


def confirmed_login_required(account: AccountState) -> bool:
    """Separate an explicit logout from a missing browser runtime."""
    return account.status is AccountStatus.NEEDS_LOGIN and not any(
        marker in (account.error or "").lower()
        for marker in ("browser profile", "not configured", "is not connected", "browser_connect")
    )
