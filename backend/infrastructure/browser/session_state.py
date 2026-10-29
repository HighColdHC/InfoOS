"""Read/write ~/.infoos/browser-session.json with a short in-process cache.

The JSON is the single source of truth for "is there a live dedicated
InfoOS Chrome session?". It's written by infoos/launcher/infoos_chrome.sh
and read by all InfoOS processes (Web / scheduler / CLI).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from backend.infrastructure.runtime.environment import browser_session_json_path

JSON_PATH = browser_session_json_path()
CACHE_TTL_SECONDS = 5.0
BINDING_PROOF = "new_context_after_dedicated_launch"
SUPERVISED_CONTAINER_BINDING_PROOF = "supervised_container_dedicated_profile"
BINDING_PROOFS = frozenset({BINDING_PROOF, SUPERVISED_CONTAINER_BINDING_PROOF})
_CACHE_LOADED_AT: float | None = None
_CACHE_VALUE: "BrowserSessionState | None" = None


@dataclass(frozen=True)
class BrowserSessionState:
    schema_version: int
    profile_dir: str
    context_id: str
    alias: str
    started_at: str
    last_health_at: str
    last_health_status: str  # "ready" | "blocked" | "unknown"
    chrome_pid: int | None = None
    binding_proof: str | None = None


def read() -> BrowserSessionState | None:
    """Read JSON; cached for CACHE_TTL_SECONDS; None on missing/corrupt."""
    global _CACHE_LOADED_AT, _CACHE_VALUE
    now = time.monotonic()
    if _CACHE_LOADED_AT is not None and now - _CACHE_LOADED_AT < CACHE_TTL_SECONDS:
        return _CACHE_VALUE
    _CACHE_VALUE = _read_uncached()
    _CACHE_LOADED_AT = now
    return _CACHE_VALUE


def read_fresh() -> BrowserSessionState | None:
    """Bypass the cache after an external launcher updates the JSON file."""
    _cache_clear()
    return read()


def _read_uncached() -> BrowserSessionState | None:
    if not JSON_PATH.exists():
        return None
    try:
        data = json.loads(JSON_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("schema_version") not in {1, 2}:
        return None
    try:
        return BrowserSessionState(
            schema_version=data["schema_version"],
            profile_dir=data["profile_dir"],
            context_id=data["context_id"],
            alias=data["alias"],
            started_at=data["started_at"],
            last_health_at=data["last_health_at"],
            last_health_status=data["last_health_status"],
            chrome_pid=data.get("chrome_pid"),
            binding_proof=data.get("binding_proof"),
        )
    except KeyError:
        return None


def write(state: BrowserSessionState) -> None:
    """Persist state. Intended for tests + single-field updates; the launcher
    script writes the JSON directly via shell and should not call this."""
    JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(state.__dict__, indent=2))
    _cache_clear()


def is_alive(state: BrowserSessionState | None) -> bool:
    """True iff state is a valid BrowserSessionState bound to infoos-fetch alias."""
    if state is None:
        return False
    if state.schema_version not in {1, 2}:
        return False
    if state.alias != "infoos-fetch":
        return False
    if not all([state.profile_dir, state.context_id]):
        return False
    return True


def is_proven_dedicated_binding(
    state: BrowserSessionState | None,
    *,
    profile_dir: Path,
) -> bool:
    """Require the launcher-owned schema-v2 proof used by production fetches."""
    if state is None:
        return False
    return (
        state.schema_version == 2
        and state.profile_dir == str(profile_dir)
        and state.alias == "infoos-fetch"
        and bool(state.context_id)
        and isinstance(state.chrome_pid, int)
        and state.chrome_pid > 0
        and state.binding_proof in BINDING_PROOFS
        and state.last_health_status == "ready"
    )


def _cache_clear() -> None:
    """Clear the cache. For tests."""
    global _CACHE_LOADED_AT, _CACHE_VALUE
    _CACHE_LOADED_AT = None
    _CACHE_VALUE = None
