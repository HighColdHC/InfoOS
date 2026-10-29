"""Small process-local abuse guard for the single-Web-process appliance MVP."""
from __future__ import annotations

from collections import defaultdict, deque
import os
from threading import Lock
from time import monotonic


_LOCK = Lock()
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def capture_rate_limits() -> tuple[int, int]:
    return (
        _positive_env("INFOOS_CAPTURE_TOKEN_REQUESTS_PER_MINUTE", 30),
        _positive_env("INFOOS_CAPTURE_SOURCE_REQUESTS_PER_MINUTE", 60),
    )


def admit_capture_request(token_id: str, source_address: str) -> bool:
    token_limit, source_limit = capture_rate_limits()
    now = monotonic()
    with _LOCK:
        return _admit(f"token:{token_id}", token_limit, now) and _admit(
            f"source:{source_address}",
            source_limit,
            now,
        )


def _admit(key: str, limit: int, now: float) -> bool:
    bucket = _BUCKETS[key]
    cutoff = now - 60
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default
