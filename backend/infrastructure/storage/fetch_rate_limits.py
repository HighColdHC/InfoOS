from __future__ import annotations

from backend.domain.timestamps import format_utc_seconds_z, parse_utc

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from backend.infrastructure.storage.database import connect_existing, connect_writable, default_database_path


@dataclass(frozen=True)
class FetchRateReservation:
    allowed: bool
    throttle_key: str
    next_run_at: str | None = None
    reason: str | None = None
    previous_reserved_at: str | None = None


class FetchRateLimitRepository:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.db_path = default_database_path(self.base_dir)

    def reserve(
        self,
        *,
        throttle_key: str,
        platform: str,
        identity_id: str,
        now: str,
        min_gap_seconds: int,
        hourly_budget: int,
        daily_budget: int,
    ) -> FetchRateReservation:
        current = parse_utc(now)
        hour_start = current.replace(minute=0, second=0, microsecond=0)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        now_iso = format_utc_seconds_z(current)
        hour_iso = format_utc_seconds_z(hour_start)
        day_iso = format_utc_seconds_z(day_start)

        conn = connect_writable(self.db_path)
        try:
            conn.execute("begin immediate")
            row = conn.execute(
                "select * from fetch_rate_limit_state where throttle_key = ?",
                (throttle_key,),
            ).fetchone()
            if row:
                next_available_at = row["next_available_at"]
                if next_available_at and parse_utc(next_available_at) > current:
                    conn.commit()
                    return FetchRateReservation(
                        allowed=False,
                        throttle_key=throttle_key,
                        next_run_at=format_utc_seconds_z(parse_utc(next_available_at)),
                        reason="min_gap",
                    )
                hour_count = row["hour_count"] if row["hour_window_start"] == hour_iso else 0
                day_count = row["day_count"] if row["day_window_start"] == day_iso else 0
            else:
                hour_count = 0
                day_count = 0

            if hourly_budget > 0 and hour_count >= hourly_budget:
                defer_until = hour_start + timedelta(hours=1)
                conn.commit()
                return FetchRateReservation(
                    allowed=False,
                    throttle_key=throttle_key,
                    next_run_at=format_utc_seconds_z(defer_until),
                    reason="hourly_budget",
                )
            if daily_budget > 0 and day_count >= daily_budget:
                defer_until = day_start + timedelta(days=1)
                conn.commit()
                return FetchRateReservation(
                    allowed=False,
                    throttle_key=throttle_key,
                    next_run_at=format_utc_seconds_z(defer_until),
                    reason="daily_budget",
                )

            next_available = current + timedelta(seconds=max(0, int(min_gap_seconds)))
            conn.execute(
                """
                insert into fetch_rate_limit_state (
                  throttle_key, platform, identity_id, next_available_at,
                  hour_window_start, hour_count, day_window_start, day_count, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(throttle_key) do update set
                  platform=excluded.platform,
                  identity_id=excluded.identity_id,
                  next_available_at=excluded.next_available_at,
                  hour_window_start=excluded.hour_window_start,
                  hour_count=excluded.hour_count,
                  day_window_start=excluded.day_window_start,
                  day_count=excluded.day_count,
                  updated_at=excluded.updated_at
                """,
                (
                    throttle_key,
                    platform,
                    identity_id,
                    format_utc_seconds_z(next_available),
                    hour_iso,
                    hour_count + 1,
                    day_iso,
                    day_count + 1,
                    now_iso,
                ),
            )
            conn.commit()
            return FetchRateReservation(
                allowed=True, throttle_key=throttle_key,
                previous_reserved_at=row["updated_at"] if row else None,
            )
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def peek(
        self,
        *,
        throttle_key: str,
        now: str,
        hourly_budget: int,
        daily_budget: int,
    ) -> FetchRateReservation:
        """Read current eligibility without consuming or advancing a budget."""
        current = parse_utc(now)
        hour_start = current.replace(minute=0, second=0, microsecond=0)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        hour_iso = format_utc_seconds_z(hour_start)
        day_iso = format_utc_seconds_z(day_start)
        with connect_existing(self.db_path) as conn:
            row = conn.execute(
                "select * from fetch_rate_limit_state where throttle_key = ?",
                (throttle_key,),
            ).fetchone()
        if not row:
            return FetchRateReservation(allowed=True, throttle_key=throttle_key)
        next_available_at = row["next_available_at"]
        if next_available_at and parse_utc(next_available_at) > current:
            return FetchRateReservation(
                allowed=False,
                throttle_key=throttle_key,
                next_run_at=format_utc_seconds_z(parse_utc(next_available_at)),
                reason="min_gap",
            )
        hour_count = row["hour_count"] if row["hour_window_start"] == hour_iso else 0
        if hourly_budget > 0 and hour_count >= hourly_budget:
            return FetchRateReservation(
                allowed=False,
                throttle_key=throttle_key,
                next_run_at=format_utc_seconds_z(hour_start + timedelta(hours=1)),
                reason="hourly_budget",
            )
        day_count = row["day_count"] if row["day_window_start"] == day_iso else 0
        if daily_budget > 0 and day_count >= daily_budget:
            return FetchRateReservation(
                allowed=False,
                throttle_key=throttle_key,
                next_run_at=format_utc_seconds_z(day_start + timedelta(days=1)),
                reason="daily_budget",
            )
        return FetchRateReservation(allowed=True, throttle_key=throttle_key)
