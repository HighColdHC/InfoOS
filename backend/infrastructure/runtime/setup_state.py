"""Setup state persistence with atomic writes.

The setup state lives at ~/.infoos/setup-state.json. It is shared between
the InfoOS web UI and the CLI ``setup`` subcommand. Writes are atomic
(write-temp + rename) so a
crash mid-write cannot leave a half-written file.

The state is a thin dataclass; ``raw`` keeps the original dict so
forward-compatible fields added in future versions are preserved
across save/load cycles.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import fcntl
from pathlib import Path
from typing import Any, Callable, Iterator


_SETUP_STATE_THREAD_LOCK = threading.RLock()


class AccountStatus(str, Enum):
    UNKNOWN = "unknown"
    NEEDS_LOGIN = "needs_login"
    VERIFIED = "verified"
    SKIPPED = "skipped"
    FAILED = "failed"


COMPONENT_IDS = (
    "dedicated_chrome",
    "opencli_bridge",
    "yt_dlp",
    "gallery_dl",
    "ffmpeg",
    "local_asr",
    "sqlite",
)


@dataclass
class ComponentState:
    status: str = "unknown"
    version: str | None = None
    last_checked: str | None = None
    detail_code: str | None = None


@dataclass
class ChromeState:
    binary_path: str | None = None
    version: str | None = None
    profile_dir: str | None = None
    running: bool = False


@dataclass
class SessionState:
    """Browser-session contextId binding, written by the setup wizard
    so the runtime fetch path knows which OpenCLI profile to use."""
    context_id: str | None = None
    alias: str | None = None
    bound_at: str | None = None


@dataclass
class ExtensionState:
    loaded: bool = False
    id: str | None = None
    manifest_version: int | None = None
    verified_at: str | None = None


@dataclass
class AccountState:
    status: AccountStatus = AccountStatus.UNKNOWN
    last_checked: str | None = None
    error: str | None = None
    persisted: bool = False
    persistence_checked_at: str | None = None
    persistence_stage: str | None = None
    persistence_error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class FirstFetchState:
    ok: bool | None = None
    fetched_count: int | None = None
    ran_at: str | None = None


@dataclass
class SetupState:
    version: int
    current_step: str
    completed_steps: list[str]
    chrome: ChromeState
    extension: ExtensionState
    session: SessionState
    accounts: dict[str, AccountState]
    components: dict[str, ComponentState]
    component_snapshot_at: str | None
    first_fetch: FirstFetchState
    wizard_opens: int
    wizard_last_seen_step: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SetupState":
        accounts_raw = raw.get("accounts") or {}
        accounts: dict[str, AccountState] = {}
        for platform, entry in accounts_raw.items():
            if isinstance(entry, AccountStatus):
                accounts[platform] = AccountState(status=entry)
            elif isinstance(entry, dict):
                try:
                    status = AccountStatus(entry.get("status", "unknown"))
                except ValueError:
                    status = AccountStatus.UNKNOWN
                accounts[platform] = AccountState(
                    status=status,
                    last_checked=entry.get("last_checked"),
                    error=entry.get("error"),
                    persisted=entry.get("persisted") is True,
                    persistence_checked_at=entry.get("persistence_checked_at"),
                    persistence_stage=entry.get("persistence_stage"),
                    persistence_error=entry.get("persistence_error"),
                    raw=dict(entry),
                )
            else:
                accounts[platform] = AccountState()
        for platform in ("x", "youtube", "reddit", "xiaohongshu", "douyin", "zsxq"):
            if platform not in accounts:
                accounts[platform] = AccountState(status=AccountStatus.UNKNOWN)
        components_raw = raw.get("components") or {}
        components: dict[str, ComponentState] = {}
        for component_id in COMPONENT_IDS:
            entry = components_raw.get(component_id)
            if not isinstance(entry, dict):
                components[component_id] = ComponentState()
                continue
            components[component_id] = ComponentState(
                status=str(entry.get("status") or "unknown"),
                version=(str(entry["version"]) if entry.get("version") is not None else None),
                last_checked=entry.get("last_checked"),
                detail_code=entry.get("detail_code"),
            )
        return cls(
            version=int(raw.get("version", 1)),
            current_step=raw.get("current_step", "fresh"),
            completed_steps=list(raw.get("completed_steps", [])),
            chrome=ChromeState(**{k: v for k, v in (raw.get("chrome") or {}).items() if k in ChromeState.__dataclass_fields__}),
            extension=ExtensionState(**{k: v for k, v in (raw.get("extension") or {}).items() if k in ExtensionState.__dataclass_fields__}),
            session=SessionState(**{k: v for k, v in (raw.get("session") or {}).items() if k in SessionState.__dataclass_fields__}),
            accounts=accounts,
            components=components,
            component_snapshot_at=raw.get("component_snapshot_at"),
            first_fetch=FirstFetchState(
                ok=(raw.get("first_fetch") or {}).get("ok"),
                fetched_count=(raw.get("first_fetch") or {}).get("fetched_count"),
                ran_at=(raw.get("first_fetch") or {}).get("ran_at"),
            ),
            wizard_opens=int(raw.get("wizard_opens", 0)),
            wizard_last_seen_step=raw.get("wizard_last_seen_step"),
            raw=dict(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        merged = {
            **self.raw,
            "version": self.version,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps,
            "chrome": {
                "binary_path": self.chrome.binary_path,
                "version": self.chrome.version,
                "profile_dir": self.chrome.profile_dir,
                "running": self.chrome.running,
            },
            "extension": {
                "loaded": self.extension.loaded,
                "id": self.extension.id,
                "manifest_version": self.extension.manifest_version,
                "verified_at": self.extension.verified_at,
            },
            "session": {
                "context_id": self.session.context_id,
                "alias": self.session.alias,
                "bound_at": self.session.bound_at,
            },
            "accounts": {
                platform: {
                    **account.raw,
                    "status": account.status.value,
                    "last_checked": account.last_checked,
                    "error": account.error,
                    "persisted": account.persisted,
                    "persistence_checked_at": account.persistence_checked_at,
                    "persistence_stage": account.persistence_stage,
                    "persistence_error": account.persistence_error,
                }
                for platform, account in self.accounts.items()
            },
            "components": {
                component_id: {
                    "status": component.status,
                    "version": component.version,
                    "last_checked": component.last_checked,
                    "detail_code": component.detail_code,
                }
                for component_id, component in self.components.items()
                if component_id in COMPONENT_IDS
            },
            "component_snapshot_at": self.component_snapshot_at,
            "first_fetch": {
                "ok": self.first_fetch.ok,
                "fetched_count": self.first_fetch.fetched_count,
                "ran_at": self.first_fetch.ran_at,
            },
            "wizard_opens": self.wizard_opens,
            "wizard_last_seen_step": self.wizard_last_seen_step,
        }
        return merged


DEFAULT_STATE = SetupState.from_dict({})


def load_state(path: Path) -> SetupState:
    if not path.exists():
        return SetupState.from_dict({})
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return SetupState.from_dict({})
    if not isinstance(raw, dict):
        return SetupState.from_dict({})
    return SetupState.from_dict(raw)


def save_state(path: Path, state: SetupState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@contextmanager
def setup_state_lock(path: Path) -> Iterator[None]:
    """Serialize setup-state read/modify/write across threads and processes."""
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _SETUP_STATE_THREAD_LOCK:
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update_account_state(
    path: Path,
    platform: str,
    update: Callable[[AccountState], AccountState],
) -> AccountState:
    """Atomically update one account without losing concurrent account writes."""
    with setup_state_lock(path):
        state = load_state(path)
        account = update(state.accounts.get(platform, AccountState()))
        state.accounts[platform] = account
        save_state(path, state)
        return account
