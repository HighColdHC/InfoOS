"""Read-only cadence suggestions from recently discovered source output."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import TypedDict

from backend.domain.backfill import is_backfill
from backend.domain.sources.registry import PLATFORM_REGISTRY
from backend.infrastructure.storage.database import connect_readonly, default_database_path
from backend.infrastructure.storage.sources import SourceRow


class CadenceSuggestion(TypedDict):
    suggested_cadence_hours: float | None
    new_items_7d: int
    reason: str


def suggest_source_cadences(base_dir: Path | str, sources: list[SourceRow]) -> dict[str, CadenceSuggestion]:
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")
    counts = {source["source_id"]: 0 for source in sources}
    if counts:
        placeholders = ", ".join("?" for _ in counts)
        with connect_readonly(default_database_path(base_dir)) as conn:
            rows = conn.execute(
                f"""select ri.source_id, ri.published_at, ri.first_seen_at,
                          exists(select 1 from acquisition_checkpoints ac
                                 where ac.run_id = ri.run_id and ac.initial_fetch = 1) as initial_fetch
                   from raw_items ri
                   where ri.first_seen_at >= ? and ri.first_seen_at <= ?
                     and ri.source_id in ({placeholders})
                     and ri.first_seen_at > coalesce((
                       select min(sc.calibrated_at) from source_calibrations sc
                       where sc.source_id = ri.source_id and sc.result = 'success'
                     ), '')""",
                (cutoff, now.isoformat(timespec="seconds").replace("+00:00", "Z"), *counts),
            )
            for row in rows:
                if not is_backfill(
                    row["published_at"], row["first_seen_at"], bool(row["initial_fetch"])
                )[0]:
                    counts[row["source_id"]] += 1
    return {source["source_id"]: _suggest(source, counts[source["source_id"]]) for source in sources}


def _suggest(source: SourceRow, count: int) -> CadenceSuggestion:
    platform = source["platform"]
    definition = PLATFORM_REGISTRY.get(platform)
    options = sorted(definition.cadence_options) if definition else []
    if not options:
        return {"suggested_cadence_hours": None, "new_items_7d": count, "reason": "平台没有可用的采集间隔"}
    min_gap = _rate_limit_floor(source, platform)
    platform_floor = 5.0 if definition and definition.requires_browser_session else 0.5
    eligible = [value for value in options if value >= max(platform_floor, min_gap)]
    if not eligible:
        return {"suggested_cadence_hours": None, "new_items_7d": count, "reason": "平台限流下无可用的采集间隔"}
    if count <= 1:
        selected = eligible[-1]
        reason = "近 7 天有效新增极少，建议使用平台允许的最长间隔"
    else:
        target = 24.0 / ((count / 7.0) * 3.0)
        selected = min(eligible, key=lambda value: (abs(value - target), -value))
        reason = f"近 7 天有效新增 {count} 条，按日均产出估算并匹配平台允许的间隔"
    return {"suggested_cadence_hours": selected, "new_items_7d": count, "reason": reason}


def _rate_limit_floor(source: SourceRow, platform: str) -> float:
    policy = source["raw_metadata"].get("fetch_rate_policy")
    if isinstance(policy, dict) and policy.get("enabled") is False:
        return 0.0
    if not ((isinstance(policy, dict) and bool(policy)) or platform in {"x", "youtube"}):
        return 0.0
    configured = policy if isinstance(policy, dict) else {}
    defaults = {"x": (12, 120, 800), "youtube": (8, 120, 800)}
    default_gap, default_hourly, default_daily = defaults.get(platform, (0, 0, 0))

    def number(key: str, default: int) -> int:
        value = configured.get(key) if key in configured else os.environ.get(f"INFOOS_{platform.upper()}_FETCH_{key.upper()}")
        try:
            return max(0, int(value)) if value is not None else default
        except (TypeError, ValueError):
            return default

    gap = number("min_gap_seconds", default_gap) / 3600
    hourly = number("hourly_budget", default_hourly)
    daily = number("daily_budget", default_daily)
    return max(gap, 1 / hourly if hourly else 0, 24 / daily if daily else 0)
