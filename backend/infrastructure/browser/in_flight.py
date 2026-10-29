"""Reference-counted in-flight state for browser acquisition work.

The watchdog still sees an active fetch through the state file's existence,
but concurrent task batches cannot erase each other's state on exit.
"""
from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from backend.infrastructure.runtime.environment import in_flight_lock_path

LOCK_PATH = in_flight_lock_path()


def is_in_flight() -> bool:
    """True while at least one non-stale owner holds an in-flight section."""
    payload = read_lock_payload()
    return bool(payload and int(payload.get("active_count") or 0) > 0)


def read_lock_payload() -> dict[str, Any] | None:
    """Read current in-flight JSON after removing dead-process owners."""
    with _state_lock():
        payload = _read_payload()
        if payload is None:
            return None
        state = _normalized_live_state(payload)
        _persist_or_remove(state)
        return state or None


@contextmanager
def in_flight_section(owner: str, task_count: int = 0) -> Iterator[Path]:
    """Add one in-flight owner and remove exactly that owner on exit."""
    entry = {
        "entry_id": uuid4().hex,
        "owner": owner,
        "pid": os.getpid(),
        "task_count": task_count,
        "started_at": utc_now_seconds_z(),
    }
    with _state_lock():
        state = _normalized_live_state(_read_payload())
        state["owners"].append(entry)
        _persist_or_remove(state)
    try:
        yield LOCK_PATH
    finally:
        with _state_lock():
            state = _normalized_live_state(_read_payload())
            state["owners"] = [
                current for current in state["owners"]
                if current.get("entry_id") != entry["entry_id"]
            ]
            _persist_or_remove(state)


@contextmanager
def _state_lock() -> Iterator[None]:
    """Serialize state-file read/modify/write without sharing OpenCLI's lock."""
    guard_path = LOCK_PATH.with_name(f"{LOCK_PATH.name}.state")
    guard_path.parent.mkdir(parents=True, exist_ok=True)
    with guard_path.open("a+", encoding="utf-8") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


def _read_payload() -> dict[str, Any] | None:
    if not LOCK_PATH.exists():
        return None
    try:
        value = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _normalized_live_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    owners = []
    if payload:
        raw_owners = payload.get("owners")
        if isinstance(raw_owners, list):
            owners = [entry for entry in raw_owners if isinstance(entry, dict) and _owner_is_alive(entry)]
        elif _owner_is_alive(payload):
            # Migrate a pre-reference-count payload while it is still active.
            owners = [{
                "entry_id": str(payload.get("entry_id") or uuid4().hex),
                "owner": str(payload.get("owner") or "unknown"),
                "pid": payload.get("pid"),
                "task_count": int(payload.get("task_count") or 0),
                "started_at": str(payload.get("started_at") or utc_now_seconds_z()),
            }]
    return _state_for_owners(owners)


def _state_for_owners(owners: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "active_count": len(owners),
        "owners": owners,
        "updated_at": utc_now_seconds_z(),
    }
    if owners:
        # Preserve the compact payload fields consumed by existing operators.
        state.update({key: owners[-1][key] for key in ("owner", "pid", "task_count", "started_at")})
    return state


def _persist_or_remove(state: dict[str, Any]) -> None:
    if not state["owners"]:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
        return
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = LOCK_PATH.with_name(f".{LOCK_PATH.name}.{uuid4().hex}.tmp")
    try:
        temporary_path.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary_path, LOCK_PATH)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _owner_is_alive(owner: dict[str, Any]) -> bool:
    pid = owner.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
