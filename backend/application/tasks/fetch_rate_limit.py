from __future__ import annotations

from backend.domain.timestamps import format_utc_seconds_z, parse_utc, utc_now_seconds_z

import random
from dataclasses import dataclass
from pathlib import Path

from backend.infrastructure.runtime.fetch_rate_policy import resolve_fetch_policy as _resolve_policy
from backend.infrastructure.storage.repositories import FetchRateLimitRepository, SourceRepository
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository

_X_SEARCH_BACKOFF_WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class FetchThrottleDecision:
    allowed: bool
    throttle_key: str | None = None
    next_run_at: str | None = None
    reason: str | None = None
    pacing: dict | None = None


def reserve_fetch_slot(base_dir: str | Path, *, source_id: str, now: str | None = None) -> FetchThrottleDecision:
    source = SourceRepository(base_dir).get_source(source_id)
    if not source:
        return FetchThrottleDecision(allowed=True)
    policy = _resolve_policy(source)
    if policy is None:
        return FetchThrottleDecision(allowed=True)
    reserved_at = now or utc_now_seconds_z()
    x_search = source.get("platform") == "x" and source.get("source_scope") == "research"
    backoff = x_search and _recent_x_failure(base_dir, reserved_at)
    multiplier = 2 if backoff else 1
    jitter = policy["jitter_seconds"] * multiplier
    min_gap = policy["min_gap_seconds"] * multiplier + _jitter(jitter)
    reservation = FetchRateLimitRepository(base_dir).reserve(
        throttle_key=policy["throttle_key"],
        platform=policy["platform"],
        identity_id=policy["identity_id"],
        now=reserved_at,
        min_gap_seconds=min_gap,
        hourly_budget=policy["hourly_budget"],
        daily_budget=policy["daily_budget"],
    )
    pacing = None
    if x_search and reservation.allowed:
        previous = reservation.previous_reserved_at
        pacing = {
            "version": 1,
            "throttle_key": reservation.throttle_key,
            "reserved_at": format_utc_seconds_z(parse_utc(reserved_at)),
            "previous_reserved_at": previous,
            "gap_since_previous_seconds": (parse_utc(reserved_at) - parse_utc(previous)).total_seconds()
                if previous else None,
            "min_gap_seconds": min_gap,
            "base_min_gap_seconds": policy["min_gap_seconds"],
            "jitter_seconds": jitter,
            "estimated_pages": policy["estimated_pages"],
            "recent_failure_backoff": backoff,
        }
    return FetchThrottleDecision(
        allowed=reservation.allowed,
        throttle_key=reservation.throttle_key,
        next_run_at=reservation.next_run_at,
        reason=reservation.reason,
        pacing=pacing,
    )


def _recent_x_failure(base_dir: str | Path, now: str) -> bool:
    circuit = RuntimeCircuitRepository(base_dir).get("platform", "x")
    if circuit is None or circuit["last_failure_at"] is None:
        return False
    last_failure = parse_utc(circuit["last_failure_at"])
    return 0 <= (parse_utc(now) - last_failure).total_seconds() <= _X_SEARCH_BACKOFF_WINDOW_SECONDS


def peek_fetch_slot(base_dir: str | Path, *, source_id: str, now: str | None = None) -> FetchThrottleDecision:
    """Check a source's shared throttle without reserving capacity."""
    source = SourceRepository(base_dir).get_source(source_id)
    if not source:
        return FetchThrottleDecision(allowed=True)
    policy = _resolve_policy(source)
    if policy is None:
        return FetchThrottleDecision(allowed=True)
    reservation = FetchRateLimitRepository(base_dir).peek(
        throttle_key=policy["throttle_key"],
        now=now or utc_now_seconds_z(),
        hourly_budget=policy["hourly_budget"],
        daily_budget=policy["daily_budget"],
    )
    return FetchThrottleDecision(
        allowed=reservation.allowed,
        throttle_key=reservation.throttle_key,
        next_run_at=reservation.next_run_at,
        reason=reservation.reason,
    )


def _jitter(seconds: int) -> int:
    if seconds <= 0:
        return 0
    return int(random.uniform(0, seconds))
