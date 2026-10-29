"""Short-lived authorization for the same-origin Browser Console gateway."""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

COOKIE_NAME = "infoos_browser_console"
_AUDIT = logging.getLogger("infoos.browser_console.audit")
_LOCK = threading.Lock()
_GRANTS: dict[str, "_Grant"] = {}


@dataclass
class _Grant:
    issued_at: float
    last_seen_at: float


def issue_grant(*, now: float | None = None) -> str:
    issued_at = time.monotonic() if now is None else now
    token = secrets.token_urlsafe(32)
    with _LOCK:
        _prune_locked(issued_at)
        _GRANTS[token] = _Grant(issued_at=issued_at, last_seen_at=issued_at)
    _audit("issued", token)
    return token


def authorize_grant(
    token: str,
    *,
    origin: str,
    referer: str,
    forwarded_host: str,
    forwarded_proto: str,
    now: float | None = None,
    refresh: bool = True,
) -> bool:
    checked_at = time.monotonic() if now is None else now
    if not token or not _same_origin(origin or referer, forwarded_host, forwarded_proto):
        _audit("denied_origin", token)
        return False
    with _LOCK:
        _prune_locked(checked_at)
        grant = _GRANTS.get(token)
        if grant is None:
            _audit("denied_expired", token)
            return False
        if refresh:
            grant.last_seen_at = checked_at
    _audit("accessed", token)
    return True


def grant_remaining_seconds(token: str, *, now: float | None = None) -> float:
    """Return the server-side idle/absolute lifetime left without refreshing it."""
    checked_at = time.monotonic() if now is None else now
    with _LOCK:
        _prune_locked(checked_at)
        grant = _GRANTS.get(token)
        if grant is None:
            return 0.0
        idle_left = _positive_seconds(
            "INFOOS_BROWSER_CONSOLE_IDLE_SECONDS", 600
        ) - (checked_at - grant.last_seen_at)
        absolute_left = cookie_max_age_seconds() - (checked_at - grant.issued_at)
        return max(0.0, min(idle_left, absolute_left))


def cookie_max_age_seconds() -> int:
    return _positive_seconds("INFOOS_BROWSER_CONSOLE_MAX_SECONDS", 1800)


def reset_grants_for_tests() -> None:
    with _LOCK:
        _GRANTS.clear()


def _prune_locked(now: float) -> None:
    idle_seconds = _positive_seconds("INFOOS_BROWSER_CONSOLE_IDLE_SECONDS", 600)
    max_seconds = cookie_max_age_seconds()
    expired = [
        token
        for token, grant in _GRANTS.items()
        if now - grant.last_seen_at > idle_seconds or now - grant.issued_at > max_seconds
    ]
    for token in expired:
        del _GRANTS[token]


def _positive_seconds(name: str, fallback: int) -> int:
    try:
        value = int(os.environ.get(name, str(fallback)))
    except ValueError:
        return fallback
    return value if value > 0 else fallback


def _same_origin(candidate: str, host: str, proto: str) -> bool:
    if not candidate or not host:
        return False
    parsed = urlsplit(candidate)
    return parsed.scheme == proto and parsed.netloc == host


def _audit(action: str, token: str) -> None:
    fingerprint = hashlib.sha256(token.encode()).hexdigest()[:12] if token else "missing"
    _AUDIT.info("browser_console action=%s grant=%s", action, fingerprint)
