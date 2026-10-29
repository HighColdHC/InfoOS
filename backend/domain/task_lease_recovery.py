"""Pure lease-expiry retry schedule."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone


def lease_recovery_next_run_at(now: str, recovered_attempt: int) -> str:
    """Bound the delay after an execution lost its worker lease."""
    return _add_seconds(now, min(60 * 2 ** max(0, recovered_attempt - 1), 1800))


def _add_seconds(now: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=int(seconds))).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

