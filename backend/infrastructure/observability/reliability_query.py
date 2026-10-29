"""Persistent operational reliability SQLite query.

This adapter intentionally derives every value from SQLite.  It is safe to
call after a worker or web process restart; no process-local telemetry is part
of the report's truth.
"""
from __future__ import annotations

from backend.domain.timestamps import parse_utc_or_none

import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Iterable
import sqlite3
from typing import cast

from backend.domain.json_types import JsonObject
from backend.infrastructure.observability.reliability_query_types import (
    AcquisitionCounts, AcquisitionSummary, CalibrationRow, CalibrationSummary,
    CardChainCounts, EventRow, EventSqlRow, Leases, Phase4Metrics,
    PipelineSummary, QueueSummary, ReliabilityStatus, RetrySummary, RunRow,
    QueueTotalRow, SourcePlatformRow, SourceRow, TaskArtifacts, TaskRow, Thresholds, Violation,
)

from backend.domain.sources.versioning import source_fetch_input_version
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


_TERMINAL_FAILURE_STATUSES = {"failed", "blocked", "error"}
_DEFAULT_THRESHOLDS: Thresholds = {
    "public_min_success_rate": 0.98,
    "browser_min_success_rate": 0.90,
    "max_expired_leases": 0,
    "max_running_lease_periods": 2,
}


def percentile(values: list[float], percentile_value: float) -> float | None:
    """Return nearest-rank percentile for a non-empty numeric sample."""
    if not values:
        return None
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile_value must be between 0 and 100")
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil((percentile_value / 100) * len(ordered)))
    return ordered[rank - 1]


def read_reliability_status(
    base_dir: Path | str,
    *,
    now: str | None = None,
) -> ReliabilityStatus:
    """Build a restart-safe reliability summary from the canonical SQLite DB."""
    base_path = Path(base_dir)
    db_path = resolve_sqlite_path(base_path)
    current = cast(datetime, parse_utc_or_none(now)) if now else datetime.now(timezone.utc)
    generated_at = _format_timestamp(current)
    window_start = _format_timestamp(current - timedelta(days=7))
    with connect_existing(db_path) as conn:
        tasks = [cast(TaskRow, dict(row)) for row in conn.execute("select rowid as _rowid, * from tasks where updated_at >= ? or status in ('pending','running')", (window_start,)).fetchall()]
        task_types = {row["task_id"]: row["task_type"] for row in tasks}
        task_sources = {
            row["task_id"]: row["target_id"]
            for row in tasks
            if row["target_kind"] == "source"
        }
        source_platforms = {
            data["source_id"]: data["platform"]
            for row in conn.execute("select source_id, platform from sources").fetchall()
            for data in [cast(SourcePlatformRow, dict(row))]
        }
        sources = [cast(SourceRow, dict(row)) for row in conn.execute("select * from sources where source_scope='maintained' and coalesce(json_extract(raw_metadata,'$.system_managed'),0)=0").fetchall()]
        events = _event_rows(conn, since=window_start)
        runs = [cast(RunRow, dict(row)) for row in conn.execute(
            """
            select run_history.*, sources.platform
            from run_history join sources on sources.source_id = run_history.source_id
            where run_history.task_type = 'fetch' and run_history.finished_at >= ?
            """, (window_start,)
        ).fetchall()]
        intelligence = _card_chain_counts(conn, window_start)
        calibrations = [cast(CalibrationRow, dict(row)) for row in conn.execute("select * from source_calibrations").fetchall()]
        phase4_metrics = _phase4_metrics(sources, tasks, calibrations, intelligence)
        queue_totals = {data["status"]: data["n"] for row in conn.execute("select status,count(*) n from tasks group by status")
                        for data in [cast(QueueTotalRow, dict(row))]}


    queue = _queue_summary(tasks, current)
    queue["pending"] = queue_totals.get("pending", 0)
    queue["running"] = queue_totals.get("running", 0)
    queue["failed"] = queue_totals.get("failed", 0)
    queue["blocked"] = queue_totals.get("blocked", 0)
    acquisition = _acquisition_summary(runs, events, task_types, task_sources, source_platforms, current)
    pipeline = _pipeline_summary(tasks, events, task_types)
    leases: Leases = {"expired": sum(1 for task in tasks if _expired_lease(task, current))}
    browser = _event_type_counts(events, prefix="browser_")
    retries: RetrySummary = {
        "scheduled": sum(1 for event in events if event["event_type"] == "retry_scheduled"),
        "lease_recovered": sum(1 for event in events if event["event_type"] == "lease_recovered"),
        "lease_lost": sum(1 for event in events if event["event_type"] == "lease_lost"),
    }
    violations = _violations(leases)
    return {
        "generated_at": generated_at,
        "history_window_start": window_start,
        "history_window_days": 7,
        "queue": queue,
        "leases": leases,
        "acquisition": acquisition,
        "calibration": _calibration_summary(calibrations, tasks, current),
        "initial_fetch": phase4_metrics["initial_fetch"],
        "product_yield": phase4_metrics["product_yield"],
        "degradation": phase4_metrics["degradation"],
        "browser": browser,
        "retries": retries,
        "pipeline": pipeline,
        "intelligence": intelligence,
        "thresholds": {**_DEFAULT_THRESHOLDS},
        "violations": violations,
    }


def _phase4_metrics(
    sources: list[SourceRow],
    tasks: list[TaskRow],
    evidence: list[CalibrationRow],
    card_chain: CardChainCounts,
) -> Phase4Metrics:
    for task in tasks:
        task["artifacts"] = json.loads(task["artifacts_summary"])
    enabled_candidates = [
        source for source in sources
        if bool(source["enabled"]) and source["connector_status"] not in {"planned", "unsupported"}
    ]
    eligible: list[SourceRow] = []
    grandfathered: list[SourceRow] = []
    for source in enabled_candidates:
        has_initial_obligation = source["connector_status"] == "implemented_uncalibrated" or any(
            task["task_type"] == "fetch"
            and task["target_kind"] == "source"
            and task["target_id"] == source["source_id"]
            and _parse_task_artifacts(task["artifacts"])["initial_fetch"]
            for task in tasks
        )
        (eligible if has_initial_obligation else grandfathered).append(source)
    current_tasks: list[tuple[SourceRow, TaskRow]] = []
    for source in eligible:
        version = source_fetch_input_version(source)
        candidates = [
            task for task in tasks
            if task["task_type"] == "fetch"
            and task["target_kind"] == "source"
            and task["target_id"] == source["source_id"]
            and task["input_version"] == version
            and _parse_task_artifacts(task["artifacts"])["initial_fetch"]
        ]
        if candidates:
            current_tasks.append((source, sorted(candidates, key=lambda item: (item["created_at"], item["task_id"]))[0]))
    counts = Counter(task["status"] for _, task in current_tasks)
    successes = counts["success"]
    failure_by_platform: dict[str, Counter[str]] = defaultdict(Counter)
    for source, task in current_tasks:
        failure = task["last_failure_class"] or ""
        if failure:
            failure_by_platform[source["platform"] or "unknown"][failure] += 1
    evidence_by_task = {row["task_id"]: row for row in evidence}
    no_data = sum(
        1 for _, task in current_tasks
        if task["status"] == "success" and (
            _parse_task_artifacts(task["artifacts"])["no_data"]
            or _calibration_no_data(evidence_by_task.get(task["task_id"]))
        )
    )
    raw_count = card_chain["raw_items_from_successful_runs"]
    card_count = card_chain["cards_from_successful_runs"]
    degraded_media = sum(
        1 for task in tasks
        if task["task_type"] == "media_download" and _meaningful_media_artifacts(task["artifacts"])
    )
    return {
        "initial_fetch": {
            "eligible": len(eligible),
            "grandfathered": len(grandfathered),
            "accounted": len(current_tasks),
            "pending": counts["pending"],
            "running": counts["running"],
            "success": counts["success"],
            "blocked": counts["blocked"],
            "failed": counts["failed"],
            "success_rate": _rate(successes, len(eligible)),
            "no_data_success": no_data,
            "failure_classes_by_platform": {
                platform: dict(sorted(values.items())) for platform, values in sorted(failure_by_platform.items())
            },
        },
        "product_yield": {
            "successful_fetch_raw_items": raw_count,
            "cards_from_successful_fetch": card_count,
            "fetch_to_card_yield": _rate(card_count, raw_count),
        },
        "degradation": {"media": degraded_media},
    }


def _json_object(value: object) -> JsonObject:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(cast(str, value) or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _calibration_no_data(row: CalibrationRow | None) -> bool:
    return _json_object(row["raw_payload"] if row is not None else None).get("no_data") is True


def _parse_task_artifacts(value: JsonObject) -> TaskArtifacts:
    # Normalize at consumption, keeping non-object JSON failures lazy as before.
    return {
        "initial_fetch": value.get("initial_fetch") is True,
        "no_data": value.get("no_data") is True,
        "download_status": value.get("download_status"),
        "gap_reasons": cast(Iterable[object], value.get("gap_reasons") or []),
    }


def _meaningful_media_artifacts(artifacts: JsonObject) -> bool:
    parsed = _parse_task_artifacts(artifacts)
    if parsed["download_status"] == "skipped_no_media_expected":
        return False
    return any(str(reason) != "skipped_no_media_expected" for reason in parsed["gap_reasons"])


def _calibration_summary(
    evidence: list[CalibrationRow],
    tasks: list[TaskRow],
    current: datetime,
) -> CalibrationSummary:
    calibration_tasks = [task for task in tasks if task["task_type"] == "source_calibrate"]
    successes = sum(1 for row in evidence if row["result"] == "success")
    failures = sum(1 for row in evidence if row["result"] == "failed")
    per_platform: dict[str, Counter[str]] = defaultdict(Counter)
    for row in evidence:
        per_platform[row["platform"] or "unknown"][row["result"] or "unknown"] += 1
    return {
        "evidence": {
            "total": len(evidence),
            "success": successes,
            "failed": failures,
            "success_rate": _rate(successes, len(evidence)),
        },
        "queue": {
            "pending": sum(1 for task in calibration_tasks if task["status"] == "pending"),
            "running": sum(1 for task in calibration_tasks if task["status"] == "running"),
            "blocked": sum(1 for task in calibration_tasks if task["status"] == "blocked"),
            "failed": sum(1 for task in calibration_tasks if task["status"] == "failed"),
            "success": sum(1 for task in calibration_tasks if task["status"] == "success"),
            "auth_required": sum(
                1
                for task in calibration_tasks
                if task["status"] == "blocked"
                and task["last_failure_class"] == "platform_not_logged_in"
            ),
            "oldest_pending_seconds": _oldest_age_seconds(
                [task for task in calibration_tasks if task["status"] == "pending"],
                current,
            ),
        },
        "by_platform": {
            platform: dict(sorted(counts.items()))
            for platform, counts in sorted(per_platform.items())
        },
    }


def _event_rows(conn: sqlite3.Connection, *, since: str) -> list[EventRow]:
    rows = conn.execute("select * from task_events where occurred_at >= ? order by occurred_at, rowid", (since,)).fetchall()
    result: list[EventRow] = []
    for row in rows:
        data = cast(EventSqlRow, dict(row))
        try:
            payload = json.loads(data["payload"])
        except json.JSONDecodeError:
            payload = {}
        value = payload if isinstance(payload, dict) else {}
        source_ids = value.get("source_ids") or []
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        result.append({**data, "payload": {"source_ids": cast(Iterable[str], source_ids)}})
    return result


def _queue_summary(tasks: list[TaskRow], current: datetime) -> QueueSummary:
    pending = [task for task in tasks if task["status"] == "pending"]
    running = [task for task in tasks if task["status"] == "running"]
    return {
        "pending": len(pending),
        "running": len(running),
        "blocked": sum(1 for task in tasks if task["status"] == "blocked"),
        "failed": sum(1 for task in tasks if task["status"] == "failed"),
        "oldest_pending_seconds": _oldest_age_seconds(pending, current),
        "oldest_running_seconds": _oldest_age_seconds(running, current),
    }


def _oldest_age_seconds(tasks: list[TaskRow], current: datetime) -> float | None:
    timestamps = [parse_utc_or_none(task["started_at"] or task["created_at"]) for task in tasks]
    present = [timestamp for timestamp in timestamps if timestamp is not None]
    if not present:
        return None
    return max(0.0, (current - min(present)).total_seconds())


def _acquisition_summary(
    runs: list[RunRow],
    events: list[EventRow],
    task_types: dict[str, str],
    task_sources: dict[str, str],
    source_platforms: dict[str, str],
    current: datetime,
) -> dict[str, AcquisitionSummary]:
    cutoff_24h = current - timedelta(hours=24)
    cutoff_7d = current - timedelta(days=7)
    per_platform: dict[str, AcquisitionCounts] = defaultdict(
        lambda: {"runs_24h": 0, "successes_24h": 0, "runs_7d": 0, "successes_7d": 0, "failure_classes": Counter()}
    )
    for run in runs:
        timestamp = parse_utc_or_none(run["finished_at"])
        platform = run["platform"] or "unknown"
        if timestamp is None or timestamp > current:
            continue
        success = run["status"].lower() == "success"
        if timestamp >= cutoff_7d:
            item = per_platform[platform]
            item["runs_7d"] += 1
            item["successes_7d"] += int(success)
            if timestamp >= cutoff_24h:
                item["runs_24h"] += 1
                item["successes_24h"] += int(success)
    for event in events:
        timestamp = parse_utc_or_none(event["occurred_at"])
        if timestamp is None or timestamp < cutoff_7d or timestamp > current or not event["failure_class"]:
            continue
        for platform in _event_platforms(event, task_types, task_sources, source_platforms):
            per_platform[platform]["failure_classes"][event["failure_class"]] += 1
    return {
        platform: {
            "runs_24h": values["runs_24h"],
            "success_rate_24h": _rate(values["successes_24h"], values["runs_24h"]),
            "runs_7d": values["runs_7d"],
            "success_rate_7d": _rate(values["successes_7d"], values["runs_7d"]),
            "failure_classes": dict(sorted(values["failure_classes"].items())),
        }
        for platform, values in sorted(per_platform.items())
    }


def _event_platforms(
    event: EventRow,
    task_types: dict[str, str],
    task_sources: dict[str, str],
    source_platforms: dict[str, str],
) -> set[str]:
    payload = event["payload"]
    source_ids = payload["source_ids"]
    platforms = {source_platforms[source_id] for source_id in source_ids if source_id in source_platforms}
    if platforms:
        return platforms
    task_id = event["task_id"] or ""
    if task_types.get(task_id) == "fetch":
        source_id = task_sources.get(task_id)
        if source_id is not None and source_id in source_platforms:
            platforms.add(source_platforms[source_id])
    return platforms or {"unknown"}


def _pipeline_summary(
    tasks: list[TaskRow], events: list[EventRow], task_types: dict[str, str]
) -> PipelineSummary:
    stage_counts = Counter(task["task_type"] for task in tasks)
    by_chain: dict[str, list[EventRow]] = defaultdict(list)
    for event in events:
        chain_id = event["chain_id"]
        if chain_id:
            by_chain[chain_id].append(event)
    fetch_to_card: list[float] = []
    for chain_events in by_chain.values():
        acquisition_times = [
            parse_utc_or_none(event["occurred_at"])
            for event in chain_events
            if event["event_type"] == "acquisition_committed"
        ]
        starts = [timestamp for timestamp in acquisition_times if timestamp is not None]
        if not starts:
            continue
        started = min(starts)
        completed_by_type: dict[str, list[datetime]] = defaultdict(list)
        for event in chain_events:
            if event["event_type"] != "completed":
                continue
            timestamp = parse_utc_or_none(event["occurred_at"])
            task_type = task_types.get(event["task_id"] or "")
            if timestamp is not None and task_type:
                completed_by_type[task_type].append(timestamp)
        for task_type, bucket in (("card_build", fetch_to_card),):
            completions = [timestamp for timestamp in completed_by_type[task_type] if timestamp >= started]
            if completions:
                bucket.append((min(completions) - started).total_seconds())
    return {
        "fetch_to_card_p50_seconds": percentile(fetch_to_card, 50),
        "fetch_to_card_p95_seconds": percentile(fetch_to_card, 95),
        "stage_counts": dict(sorted(stage_counts.items())),
    }


def _card_chain_counts(conn: sqlite3.Connection, window_start: str) -> CardChainCounts:
    successful_runs = cast(int, conn.execute(
        "select count(*) from run_history where task_type = 'fetch' and status = 'success' and finished_at >= ?",
        (window_start,),
    ).fetchone()[0])
    raw_items = cast(int, conn.execute(
        """select count(*) from raw_items r join run_history h on h.run_id = r.run_id
        where h.task_type = 'fetch' and h.status = 'success' and h.finished_at >= ?""",
        (window_start,),
    ).fetchone()[0])
    cards_from_runs = cast(int, conn.execute(
        """select count(distinct c.card_id) from cards c
        join raw_items r on r.run_id = c.source_run_id
            and r.connector_id = c.raw_connector_id and r.raw_item_id = c.raw_item_id
        join run_history h on h.run_id = r.run_id
        where h.task_type = 'fetch' and h.status = 'success' and h.finished_at >= ?""",
        (window_start,),
    ).fetchone()[0])
    return {
        "successful_source_runs": successful_runs,
        "raw_items_from_successful_runs": raw_items,
        "cards_from_successful_runs": cards_from_runs,
        "raw_to_card_yield": _rate(cards_from_runs, raw_items),
        "cards": cast(int, conn.execute("select count(*) from cards").fetchone()[0]),
    }


def _event_type_counts(events: list[EventRow], *, prefix: str) -> dict[str, int]:
    counts = Counter(event["event_type"] for event in events if event["event_type"].startswith(prefix))
    return {event_type.removeprefix(prefix): value for event_type, value in sorted(counts.items())}


def _expired_lease(task: TaskRow, current: datetime) -> bool:
    return task["status"] == "running" and (parse_utc_or_none(task["lease_expires_at"]) or current) < current


def _violations(leases: Leases) -> list[Violation]:
    if leases["expired"] <= _DEFAULT_THRESHOLDS["max_expired_leases"]:
        return []
    return [{"metric": "expired_leases", "actual": leases["expired"], "maximum": _DEFAULT_THRESHOLDS["max_expired_leases"]}]


def _rate(successes: int, total: int) -> float | None:
    return round(successes / total, 4) if total else None




def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
