"""Cadence parsing and UTC timestamp helpers."""
from __future__ import annotations

from backend.domain.timestamps import format_local_naive_utc_z as format_utc, parse_utc

import re
from datetime import datetime, timedelta, timezone


NAMED_CADENCES = {
    "hourly": 60 * 60,
    "every_6_hours": 6 * 60 * 60,
    "daily": 24 * 60 * 60,
    "weekly": 7 * 24 * 60 * 60,
}






def cadence_to_seconds(cadence: str) -> int:
    if cadence in NAMED_CADENCES:
        return NAMED_CADENCES[cadence]
    match = re.fullmatch(r"every_(\d+)_(minute|minutes|hour|hours|day|days)", cadence)
    if match is None:
        raise ValueError(f"unsupported cadence: {cadence}")
    amount = int(match.group(1))
    if amount <= 0:
        raise ValueError(f"unsupported cadence: {cadence}")
    multipliers = {
        "minute": 60,
        "minutes": 60,
        "hour": 60 * 60,
        "hours": 60 * 60,
        "day": 24 * 60 * 60,
        "days": 24 * 60 * 60,
    }
    return amount * multipliers[match.group(2)]


def compute_next_run_at(*, cadence: str, last_finished_at: str) -> str:
    last = parse_utc(last_finished_at)
    return format_utc(last + timedelta(seconds=cadence_to_seconds(cadence)))


def is_due(*, cadence: str, now: datetime, last_finished_at: str | None) -> bool:
    if last_finished_at is None:
        return True
    last = parse_utc(last_finished_at)
    elapsed = now.astimezone(timezone.utc) - last
    return elapsed.total_seconds() >= cadence_to_seconds(cadence)
