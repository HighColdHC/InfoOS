"""Pure options and Shanghai working-window scheduling for Douyin home feed."""
from __future__ import annotations

from datetime import datetime, timedelta
from random import Random
import re
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def is_douyin_home_feed(source: dict[str, Any]) -> bool:
    return source.get("platform") == "douyin" and source.get("source_type") == "home_feed"


def home_feed_options(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = {} if metadata is None else metadata
    if not isinstance(metadata, dict):
        raise ValueError("douyin home_feed raw_metadata must be an object")
    options = {}
    enabled = metadata.get("monetization_prefilter", False)
    if not isinstance(enabled, bool):
        raise ValueError("monetization_prefilter must be a boolean")
    options["monetization_prefilter"] = enabled
    for key, default, low, high in (
        ("dwell_min_seconds", 6, 0, 25), ("dwell_max_seconds", 20, 0, 25),
        ("session_interval_min_minutes", 60, 30, 720),
        ("session_interval_max_minutes", 120, 30, 720),
        ("prefilter_comment_min_count", 20, 0, 1000000),
        ("prefilter_demand_min_comments", 2, 1, 20),
    ):
        value = metadata.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} must be an integer in {low}-{high}")
        options[key] = value
    for prefix, unit in (("dwell", "seconds"), ("session_interval", "minutes")):
        if options[f"{prefix}_min_{unit}"] > options[f"{prefix}_max_{unit}"]:
            raise ValueError(f"{prefix} min must be <= max")
    hours = metadata.get("active_hours", {"start": "09:00", "end": "19:00"})
    if not isinstance(hours, dict):
        raise ValueError("active_hours must be an object with start/end HH:MM")
    for key in ("start", "end"):
        if not isinstance(hours.get(key), str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", hours[key]):
            raise ValueError("active_hours start/end must be HH:MM")
    if hours["start"] >= hours["end"]:
        raise ValueError("active_hours start must be < end")
    return {**options, "active_hours": {key: hours[key] for key in ("start", "end")}}


def _window(at: datetime, options: dict[str, Any]) -> tuple[datetime, datetime]:
    if at.tzinfo is None:
        raise ValueError("session time must include timezone")
    local = at.astimezone(SHANGHAI)
    bounds = []
    for key in ("start", "end"):
        hour, minute = map(int, options["active_hours"][key].split(":"))
        bounds.append(local.replace(hour=hour, minute=minute, second=0, microsecond=0))
    return bounds[0], bounds[1]


def first_session_at(at: datetime, metadata: dict[str, Any] | None = None) -> datetime:
    """Keep a due periodic run in-window, or move it to the next opening."""
    start, end = _window(at, home_feed_options(metadata))
    local = at.astimezone(SHANGHAI)
    if local < start:
        return start
    if local >= end:
        return start + timedelta(days=1)
    return local


def next_session_at(finished_at: datetime, metadata: dict[str, Any] | None, *, rng: Random) -> datetime:
    options = home_feed_options(metadata)
    minimum = options["session_interval_min_minutes"]
    candidate = finished_at + timedelta(minutes=rng.randint(minimum, options["session_interval_max_minutes"]))
    opening = first_session_at(candidate, options)
    if opening == candidate:
        return opening
    start, end = _window(opening, options)
    # A configured window shorter than min still must never schedule outside it.
    jitter_max = min(minimum, int((end - start).total_seconds() // 60) - 1)
    return opening + timedelta(minutes=rng.randint(0, jitter_max))
