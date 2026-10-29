"""Pure, bounded ZSXQ digest observation commands and date windows."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_DAYS = 14
DEFAULT_LIMIT = 60


def digest_recheck_options(metadata: dict[str, Any]) -> dict[str, int]:
    result = {}
    for key, default, minimum in (("digest_recheck_days", DEFAULT_DAYS, 0),
                                  ("digest_recheck_limit", DEFAULT_LIMIT, 1)):
        value = metadata.get(key, default)
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer >= {minimum}") from exc
        if isinstance(value, bool) or str(parsed) != str(value) or parsed < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")
        result[key] = parsed
    return result


def digest_list_command(group_id: str, start_date: str, end_date: str, limit: int) -> list[str]:
    return ["zsxq", "search", "*", "--group_id", group_id, "--limit", str(limit),
            "--sort", "newest", "--content-type", "essence", "--start-date", start_date,
            "--end-date", end_date, "--hydrate", "false"]


def digest_recheck_command(group_id: str, options: dict[str, int], *, now: datetime | None = None) -> list[str] | None:
    days = options["digest_recheck_days"]
    if days == 0:
        return None
    today = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai")).date()
    return digest_list_command(group_id, (today - timedelta(days=days - 1)).isoformat(),
                               today.isoformat(), options["digest_recheck_limit"])


def digest_windows(start_date: str, end_date: str, window_days: int = 7) -> list[dict[str, str]]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if start.isoformat() != start_date or end.isoformat() != end_date:
        raise ValueError("dates must use YYYY-MM-DD")
    if start > end or window_days < 1:
        raise ValueError("start_date must be <= end_date and window_days must be >= 1")
    windows = []
    while start <= end:
        last = min(end, start + timedelta(days=window_days - 1))
        windows.append({"start_date": start.isoformat(), "end_date": last.isoformat()})
        start = last + timedelta(days=1)
    return windows
