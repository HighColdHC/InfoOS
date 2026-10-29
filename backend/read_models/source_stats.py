"""Source health evidence joined with current source configuration."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NotRequired, TypedDict

from backend.domain.intelligence.rubric import QUALITY_THRESHOLD
from backend.domain.sources.zsxq import opportunity_source_for_topics
from backend.domain.intelligence.source_stats import AcquisitionStats, acquisition_stats, source_verdict
from backend.infrastructure.storage.source_stats import SourceStatsRow, aggregate_acquisition_stats, aggregate_source_stats
from backend.infrastructure.storage.sources import SourceRepository
from backend.read_models.new_cards import _stamp


class SourceStatsItem(SourceStatsRow, AcquisitionStats):
    schedule_note: NotRequired[str]
    display_name: str | None
    platform: str | None
    enabled: bool | None
    cadence_hours: float | None
    pass_rate: float | None
    coverage: float | None
    verdict: str


class SourceStatsTotals(TypedDict):
    cards: int
    evaluated: int
    passed: int
    pass_rate: float | None


class SourceStatsPage(TypedDict):
    since: str
    until: str
    min_evaluated: int
    quality_threshold: int
    totals: SourceStatsTotals
    items: list[SourceStatsItem]


def get_source_stats(base_dir: Path | str, since: str | None = None, until: str | None = None,
                     min_evaluated: int = 10) -> SourceStatsPage:
    if isinstance(min_evaluated, bool) or not isinstance(min_evaluated, int) or not 1 <= min_evaluated <= 1000:
        raise ValueError("min_evaluated must be between 1 and 1000")
    now = datetime.now(timezone.utc)
    start = _stamp(since if since is not None else (now - timedelta(days=30)).isoformat())
    end = _stamp(until if until is not None else now.isoformat())
    if start > end:
        raise ValueError("since must not be later than until")
    rows = aggregate_source_stats(base_dir, start, end)
    runs = {row["source_id"]: row for row in aggregate_acquisition_stats(base_dir, start, end)}
    card_sources = {row["source_id"] for row in rows}
    rows.extend({"source_id": source_id, "cards": 0, "evaluated": 0, "passed": 0,
                 "avg_quality": None, "avg_relevance": None, "paused": 0, "exploration": 0}
                for source_id in runs if source_id not in card_sources)
    sources = {s["source_id"]: s for s in SourceRepository(base_dir).list_sources(include_research=True)}
    totals: SourceStatsTotals = {
        "cards": sum(row["cards"] for row in rows),
        "evaluated": sum(row["evaluated"] for row in rows),
        "passed": sum(row["passed"] for row in rows),
        "pass_rate": None,
    }
    totals["pass_rate"] = round(totals["passed"] / totals["evaluated"], 3) if totals["evaluated"] else None
    items: list[SourceStatsItem] = []
    for row in rows:
        source = sources.get(row["source_id"]) if row["source_id"] is not None else None
        rate = row["passed"] / row["evaluated"] if row["evaluated"] else None
        opportunity_id = opportunity_source_for_topics(source, list(sources.values())) if source is not None else None
        item: SourceStatsItem = {**row, **acquisition_stats(runs.get(row["source_id"]) if row["source_id"] is not None else None),
                      "display_name": source["display_name"] if source else row["source_id"],
                      "platform": source["platform"] if source else None, "enabled": source["enabled"] if source else None,
                      "cadence_hours": source["cadence_hours"] if source else None,
                      "avg_quality": round(row["avg_quality"], 1) if row["avg_quality"] is not None else None,
                      "avg_relevance": round(row["avg_relevance"], 1) if row["avg_relevance"] is not None else None,
                      "pass_rate": round(rate, 3) if rate is not None else None,
                      "coverage": round(row["evaluated"] / row["cards"], 3) if row["cards"] else None,
                      "verdict": source_verdict(row["evaluated"], rate, min_evaluated)}
        if opportunity_id:
            item["schedule_note"] = f"风向标帖由 {opportunity_id} 负责"
        items.append(item)
    items.sort(key=lambda item: (item["verdict"] != "low_yield",
                               item["passed"] / item["evaluated"] if item["evaluated"] else float("inf"),
                               -item["evaluated"], item["source_id"] or ""))
    return {"since": start, "until": end, "min_evaluated": min_evaluated,
            "quality_threshold": QUALITY_THRESHOLD, "totals": totals, "items": items}
