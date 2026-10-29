"""Classify cards discovered through initial or historical collection."""
from __future__ import annotations

from datetime import timedelta

from backend.domain.timestamps import parse_utc_or_none


def is_backfill(
    published_at: str | None, first_seen_at: str | None, initial_fetch: bool
) -> tuple[bool, str | None]:
    if initial_fetch:
        return True, "initial_fetch"
    published = parse_utc_or_none(published_at)
    seen = parse_utc_or_none(first_seen_at)
    if published is None or seen is None:
        return False, None
    if published - seen > timedelta(hours=1):
        return False, "future_published_at_ignored"
    if seen - published > timedelta(hours=48):
        return True, "published_over_48h_before_discovery"
    return False, None
