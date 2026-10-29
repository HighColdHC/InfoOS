"""Read-only source health aggregates over indexed card entry windows."""
from pathlib import Path
from typing import Any, TypedDict, cast

from backend.infrastructure.storage.card_entry import ENTRY_JOIN, entry_window
from backend.infrastructure.storage.database import connect_readonly, default_database_path


class SourceStatsRow(TypedDict):
    source_id: str | None
    cards: int
    evaluated: int
    passed: int
    avg_quality: float | None
    avg_relevance: float | None
    paused: int
    exploration: int


class AcquisitionStatsRow(TypedDict):
    source_id: str
    fetch_run_count: int
    partial_run_count: int
    latest_stop_reason: object


SOURCE_STATS_SELECT = f"""select c.source_id, count(*) as cards,
    count(e.evaluation_id) as evaluated,
    coalesce(sum(e.passed_quality = 1), 0) as passed,
    avg(e.quality) as avg_quality, avg(e.relevance) as avg_relevance,
    coalesce(sum(e.attribution_kind = 'paused'), 0) as paused,
    coalesce(sum(e.attribution_kind = 'exploration'), 0) as exploration
    from {ENTRY_JOIN}
    left join card_evaluations e on e.evaluation_id = (
        select latest.evaluation_id from card_evaluations latest
        where latest.card_id = c.card_id
        order by latest.created_at desc, latest.evaluation_id desc limit 1)
    where """


def source_stats_query(since: str, until: str) -> tuple[str, list[Any]]:
    clauses, params = entry_window(since, until)
    return SOURCE_STATS_SELECT + " and ".join(clauses) + " group by c.source_id", params


def aggregate_source_stats(base_dir: Path | str, since: str, until: str) -> list[SourceStatsRow]:
    query, params = source_stats_query(since, until)
    with connect_readonly(default_database_path(base_dir)) as conn:
        rows = conn.execute(query, params).fetchall()
    return [cast(SourceStatsRow, dict(row)) for row in rows]


def aggregate_acquisition_stats(base_dir: Path | str, since: str, until: str) -> list[AcquisitionStatsRow]:
    """Count persisted FETCH runs by finish time, including runs with no new cards."""
    with connect_readonly(default_database_path(base_dir)) as conn:
        rows = conn.execute("""
            with window_runs as (
                select source_id, finished_at, run_id,
                    json_extract(raw_payload, '$.completeness_status') = 'partial' as partial,
                    json_extract(raw_payload, '$.stop_reason') as stop_reason
                from run_history where task_type = 'fetch'
                    and julianday(finished_at) >= julianday(?)
                    and julianday(finished_at) < julianday(?)
            ), ranked as (
                select *, row_number() over (
                    partition by source_id order by coalesce(partial, 0) desc, finished_at desc, run_id desc
                ) as position from window_runs
            )
            select source_id, count(*) as fetch_run_count,
                coalesce(sum(partial), 0) as partial_run_count,
                max(case when position = 1 and partial then stop_reason end) as latest_stop_reason
            from ranked group by source_id
        """, (since, until)).fetchall()
    return [cast(AcquisitionStatsRow, dict(row)) for row in rows]
