"""Shanghai-day statistics from stored acquisition, breakdown and follow-up facts."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from statistics import median
from typing import cast
from zoneinfo import ZoneInfo

from backend.domain.intelligence.opportunity import ENUM_MEANINGS, OPPORTUNITY_LENS_VERSION
from backend.domain.intelligence.opportunity_follow_up import STAGES
from backend.domain.json_types import JsonObject
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.opportunity_candidates import CANDIDATE, CURRENT_BREAKDOWN, ENTRY_JOIN
from backend.read_models._connections import shared_read_connection
from backend.read_models.cards import platform_display_label
from backend.read_models.opportunity_board_data import (
    breakdown_stages, latest_verifications, stats_breakdowns, verifications,
)
from backend.read_models.opportunity_board_types import (
    AcquisitionFunnel, CandidateRow, DailyCount, FollowUpRow, Funnel, PlatformCounts, RunRow, StatsData,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _prefilter(raw: str) -> dict[str, int] | None:
    # Historical run JSON is a system boundary, including malformed/partial rows.
    try:
        payload: JsonObject = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("prefilter_summary"), dict):
        return None
    summary = payload["prefilter_summary"]
    return {key: value if type(value := summary.get(key)) is int and value >= 0 else 0
            for key in ("checked", "matched", "unknown")}


@shared_read_connection
def stats(base_dir: Path | str, window: str = "all") -> StatsData:
    base_dir = Path(base_dir)
    now = datetime.now(timezone.utc)
    today = now.astimezone(SHANGHAI).replace(hour=0, minute=0, second=0, microsecond=0)
    start = None if window == "all" else today - timedelta(days={"7d": 6, "30d": 29}[window])
    end = today + timedelta(days=1)

    def within(stamp: str) -> bool:
        time = parse_utc(stamp)
        return (start is None or time >= start) and time < end

    all_breakdowns = stats_breakdowns(base_dir)
    selected = [row for row in all_breakdowns if within(row["created_at"])]
    records = verifications(base_dir)
    latest = latest_verifications(records)
    platforms: defaultdict[str, PlatformCounts] = defaultdict(
        lambda: {"visible": 0, "matched": 0, "broken_down": 0, "verify": 0})
    funnel: AcquisitionFunnel = {"visible": 0, "checked": 0, "matched": 0, "broken_down": len(selected),
              "verify": sum(row["next_step"] == "verify" for row in selected)}
    with InfoOSRepository(base_dir).connect_existing() as conn:
        runs = [cast(RunRow, dict(row)) for row in conn.execute("select h.visible_count,h.raw_payload,h.finished_at,s.platform "
            "from run_history h join sources s on s.source_id=h.source_id "
            "where instr(h.raw_payload, '\"prefilter_summary\"') > 0 and h.finished_at >= ?",
            # Coarse SQL prefilter only; within() below applies the exact window.
            (start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") if start is not None else "",)).fetchall()]
        events = [cast(FollowUpRow, dict(row)) for row in conn.execute("select card_id,state,created_at "
            "from opportunity_follow_up_events order by created_at,event_id")]
        profile = InterestProfileRepository(base_dir).get(connection=conn)
        profile_version = profile["profile_version"] if profile is not None else None
        # One pass over candidate JSON (the expensive part) serves both the zsxq hits and the backlog.
        candidates = [cast(CandidateRow, dict(row)) for row in conn.execute(
                                  f"select s.platform,r.first_seen_at,{CURRENT_BREAKDOWN} as current "
                                  f"from {ENTRY_JOIN} where {CANDIDATE}",
                                  (OPPORTUNITY_LENS_VERSION, profile_version)).fetchall()]
    pending = sum(not candidate["current"] for candidate in candidates)
    for run in runs:
        summary = _prefilter(run["raw_payload"])
        if summary is None or not within(run["finished_at"]):
            continue
        hit = summary["matched"] + summary["unknown"]
        funnel["visible"] += run["visible_count"]
        funnel["checked"] += summary["checked"]
        funnel["matched"] += hit
        platforms[run["platform"]]["visible"] += run["visible_count"]
        platforms[run["platform"]]["matched"] += hit
    for candidate in candidates:
        # zsxq has no per-run prefilter, so its signal posts count as hits but not as seen or checked.
        if candidate["platform"] == "zsxq" and within(candidate["first_seen_at"]):
            funnel["matched"] += 1
            platforms[candidate["platform"]]["matched"] += 1
    daily: Counter[str] = Counter()
    models: defaultdict[str, dict[str, int]] = defaultdict(lambda: {"verify": 0, "observe": 0, "skip": 0})
    risks: Counter[str] = Counter()
    steps: Counter[str] = Counter()
    for row in selected:
        platforms[row["platform"]]["broken_down"] += 1
        platforms[row["platform"]]["verify"] += row["next_step"] == "verify"
        daily[parse_utc(row["created_at"]).astimezone(SHANGHAI).date().isoformat()] += 1
        models[row["monetization_model"]][row["next_step"]] += 1
        steps[row["next_step"]] += 1
        risks.update(row["risk_flags"])
    daily_start = start
    if daily_start is None and daily:
        daily_start = datetime.fromisoformat(min(daily)).replace(tzinfo=SHANGHAI)
    days: list[DailyCount] = []
    if daily_start is not None:
        day = daily_start
        while day < end:
            key = day.date().isoformat()
            days.append({"date": key, "count": daily[key]})
            day += timedelta(days=1)
    following = {event["card_id"] for event in events if event["state"] == "following" and within(event["created_at"])}
    contacted = {v["card_id"] for v in records if within(v["created_at"])}
    full_funnel: Funnel = {**funnel, "following": len(following), "contacted": len(contacted),
        "action": sum(v["conclusion"] == "to_action" and within(v["created_at"]) for v in latest.values())}
    stages: Counter[str] = Counter()
    archives: Counter[str] = Counter()
    latest_events = {event["card_id"]: event for event in events}
    for item in breakdown_stages(all_breakdowns, latest_events, latest).values():
        if within(cast(str, item["stage_since"])):
            stages[cast(str, item["stage"])] += 1
            if item["archive_reason"] is not None:
                archives[item["archive_reason"]] += 1
    first_following: dict[str, str] = {}
    first_contact: dict[str, str] = {}
    for event in events:
        if event["state"] == "following":
            first_following.setdefault(event["card_id"], event["created_at"])
    for record in records:
        first_contact.setdefault(record["card_id"], record["created_at"])
    delays = [(parse_utc(first_contact[card_id]) - parse_utc(stamp)).total_seconds() / 86400
        for card_id, stamp in first_following.items() if card_id in first_contact and within(stamp)
        and parse_utc(first_contact[card_id]) >= parse_utc(stamp)]
    return {"window": window, "timezone": "Asia/Shanghai", "funnel": full_funnel,
        "platforms": [{"platform": key, "label": platform_display_label(key), **value}
                      for key, value in sorted(platforms.items())], "daily": days,
        "progress": {"latest_breakdown_at": max((row["created_at"] for row in all_breakdowns), default=None),
            "last_24h": sum(now - timedelta(days=1) <= parse_utc(row["created_at"]) <= now for row in all_breakdowns),
            "pending": pending},
        "next_steps": {key: steps[key] for key in ENUM_MEANINGS["next_step"]},
        "models": [{"model": key, "label": ENUM_MEANINGS["monetization_model"][key],
                    "verify": value["verify"], "observe": value["observe"], "skip": value["skip"]}
                   for key, value in sorted(models.items(), key=lambda pair: -sum(pair[1].values()))],
        "points": [[row["replicability"], row["credibility"], row["next_step"]] for row in selected],
        "risks": [{"risk": key, "label": ENUM_MEANINGS["risk_flags"][key], "count": risks[key]}
                  for key in ENUM_MEANINGS["risk_flags"]],
        "follow_up": {"stages": {key: stages[key] for key in STAGES},
            "archive": {"dismissed": archives["dismissed"], "dropped": archives["dropped"]},
            "median_contact_days": median(delays) if delays else None}}
