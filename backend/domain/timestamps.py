"""Normalize absolute source timestamps; relative or invalid values stay unknown."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
import re
from typing import Any


def utc_now() -> datetime:
    """Return the current aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_seconds_z() -> str:
    return format_utc_seconds_z(utc_now())


def utc_now_micro_z() -> str:
    """Preserve isoformat's existing auto precision (microseconds when present)."""
    return format_utc_z(utc_now())


def utc_now_offset() -> str:
    return utc_now().isoformat()


def utc_now_display_minutes() -> str:
    return utc_now().strftime("%Y-%m-%d %H:%M UTC")


def as_utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_utc(value: str) -> datetime:
    return as_utc_datetime(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def parse_iso_or_none(value: Any) -> datetime | None:
    """Parse an ISO value without changing its original timezone awareness."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_utc_or_none(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_utc(str(value))
    except ValueError:
        return None


def parse_utc_or_now(value: str | None) -> datetime:
    return parse_utc(value) if value else utc_now()


def capture_timestamp(*candidates: Any) -> str:
    """Return the first timezone-aware ISO candidate as UTC ``Z``, else now.

    captured_at is InfoOS's acquisition time. Publisher dates such as
    ``Fri Aug 07 …``, epoch seconds or localized relative text are never valid
    candidates, and naive values are rejected rather than guessed.
    """
    for candidate in candidates:
        parsed = parse_iso_or_none(candidate) if isinstance(candidate, str) else None
        if parsed is not None and parsed.tzinfo is not None:
            return format_utc_z(parsed)
    return utc_now_micro_z()


_CANONICAL_UTC_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def captured_at_needs_repair(captured: Any, published: Any) -> bool:
    """True for legacy captured_at values that are not an acquisition time.

    Old connectors stored the publisher date (any format) in captured_at, so a
    value is suspect when it is not canonical UTC ``Z`` or names the same
    instant as published_at.
    """
    text = captured if isinstance(captured, str) else ""
    parsed = parse_iso_or_none(text) if _CANONICAL_UTC_Z.fullmatch(text) else None
    if parsed is None or format_utc_z(parsed) != text:
        return True
    if published and text == published:
        return True
    instant = normalize_timestamp(published)
    return instant is not None and normalize_timestamp(text) == instant


def coerce_datetime(value: Any) -> datetime | None:
    """Keep existing datetime values unchanged; parse nonempty timestamp strings."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return parse_utc(value)
    return None


def format_utc_seconds_z(value: datetime) -> str:
    return as_utc_datetime(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def format_utc_z(value: datetime) -> str:
    return as_utc_datetime(value).isoformat().replace("+00:00", "Z")


def format_local_naive_utc_z(value: datetime) -> str:
    """Keep cadence's old local-time interpretation for naive datetimes."""
    return format_utc_z(value.astimezone(timezone.utc))


def normalize_timestamp(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{10,16}(?:\.\d+)?", text):
            seconds = float(text)
            if seconds >= 1e14:
                seconds /= 1_000_000
            elif seconds >= 1e11:
                seconds /= 1_000
            parsed = datetime.fromtimestamp(seconds, timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


@lru_cache(maxsize=65536)
def timestamp_sort_key(primary: str | None, fallback: str | None) -> str:
    """Return a UTC second-resolution key, falling back for invalid timestamps."""
    normalized = normalize_timestamp(primary) or normalize_timestamp(fallback)
    return normalized[:19] if normalized else ""
