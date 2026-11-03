"""Opportunity knowledge counts by Shanghai calendar day."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict
from zoneinfo import ZoneInfo

from backend.domain.intelligence.knowledge import RESEARCH_LABELS, RESEARCH_STATUSES
from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.infrastructure.storage.opportunity_knowledge_queries import OpportunityKnowledgeQueryRepository
from backend.read_models.cards import platform_display_label

SHANGHAI = ZoneInfo("Asia/Shanghai")


class StatsData(TypedDict):
    window: str
    total: int
    by_status: list[dict]
    by_subtype: list[dict]
    by_signal: list[dict]
    daily: list[dict]
    by_platform: list[dict]


def stats(base_dir: Path | str, window: str = "all") -> StatsData:
    today = datetime.now(timezone.utc).astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
    start = None if window == "all" else today - timedelta(days={"7d": 6, "30d": 29}[window])
    since = start.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z") if start else None
    rows = OpportunityKnowledgeQueryRepository(base_dir).stats(since=since)
    daily = []
    day = start.date() if start else datetime.fromisoformat(min(rows["daily"])).date() if rows["daily"] else None
    while day is not None and day <= today.date():
        daily.append({"date": day.isoformat(), "count": rows["daily"].get(day.isoformat(), 0)})
        day += timedelta(days=1)
    return {"window": window, "total": sum(count for _, _, count in rows["status_subtype"]),
        "by_status": [{"status": status, "label": RESEARCH_LABELS[status],
            "count": sum(count for s, _, count in rows["status_subtype"] if s == status)} for status in RESEARCH_STATUSES],
        "by_subtype": [{"subtype": subtype, "label": label,
            "count": sum(count for _, t, count in rows["status_subtype"] if t == subtype)}
            for subtype, label in KINDS["opportunity"].subtypes.items()],
        "by_signal": [{"signal": signal, "count": rows["signals"].get(signal, 0)}
            for signal in KINDS["opportunity"].fields["signal_kinds"]["enum"]],
        "daily": daily, "by_platform": [{"platform": platform, "label": platform_display_label(platform), "count": count}
            for platform, count in sorted(rows["platforms"].items(), key=lambda pair: (-pair[1], pair[0]))]}
