"""Clock and lease timing rules shared by task execution components."""
from __future__ import annotations

import os
from datetime import timedelta
from backend.domain.timestamps import utc_now_seconds_z, parse_utc, format_utc_seconds_z


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def heartbeat_interval_seconds(lease_seconds: int) -> int:
    configured = max(1, env_int("INFOOS_TASK_HEARTBEAT_SECONDS", 30))
    return min(configured, max(1, (lease_seconds - 1) // 2))


def current_iso() -> str:
    return utc_now_seconds_z()


def add_seconds(value: str, seconds: int) -> str:
    return format_utc_seconds_z(parse_utc(value) + timedelta(seconds=seconds))
