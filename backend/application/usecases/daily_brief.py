"""Build one frozen card-level brief for a Shanghai calendar day."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.domain.intelligence.daily_brief import select_knowledge_brief
from backend.infrastructure.storage.knowledge_brief_query import brief_knowledge_rows
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository

SHANGHAI = ZoneInfo("Asia/Shanghai")


def brief_window(brief_date: str) -> tuple[str, str]:
    day = date.fromisoformat(brief_date)
    end = datetime(day.year, day.month, day.day, 7, tzinfo=SHANGHAI)
    start = end - timedelta(days=1)
    return (start.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            end.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))


def build_daily_brief(base_dir: Path | str, brief_date: str, *, force: bool = False) -> dict:
    repo = DailyBriefRepository(base_dir)
    existing = repo.get(brief_date)
    if existing and not force:
        return {"status": "frozen", "brief": existing}
    profile = InterestProfileRepository(base_dir).get()
    if profile is None:
        return {"status": "skipped", "reason": "profile_missing"}
    start, end = brief_window(brief_date)
    selected = select_knowledge_brief(brief_knowledge_rows(base_dir, start, end))
    payload = {"brief_date": brief_date, "window_start": start, "window_end": end,
               "profile_version": profile["profile_version"], **selected}
    if force:
        repo.replace(payload)
    else:
        with repo.connect() as conn:
            repo.insert(conn, payload)
    return {"status": "rebuilt" if force else "generated", "brief": repo.get(brief_date)}
