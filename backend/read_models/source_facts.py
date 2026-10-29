"""Shared, read-only card and run aggregates for source projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import TypedDict, cast


class LatestRunRow(TypedDict):
    """Latest ``run_history`` row per source; timestamps are NOT NULL but may be empty."""

    source_id: str
    run_id: str
    status: str
    finished_at: str
    started_at: str
    scheduled_for: str
    error_summary: str | None
    next_run_at: str | None
    raw_payload: str


class RecentRunCounts(TypedDict):
    total: int
    success: int
    failed: int


def load_source_card_facts(
    conn: sqlite3.Connection, source_ids: list[str] | None = None,
) -> tuple[dict[str, int], set[str]]:
    """Count all card states; None selects all sources, [] selects none."""
    if source_ids == []:
        return {}, set()
    condition = "source_id is not null"
    block_condition = ""
    params = source_ids or []
    if source_ids is not None:
        condition += f" and source_id in ({', '.join('?' for _ in source_ids)})"
        block_condition = f"where card_id in (select card_id from cards where {condition})"
        params = params + params
    # Materialize only these two per-card facts before grouping by source.
    # Subtitle fallback is block metadata; card payloads have no top-level flag.
    rows = conn.execute(
        f"""with block_flags as (
              select card_id,
                     max(instr(lower(coalesce(provenance, '')), 'metadata_fallback') > 0)
                       as has_metadata_fallback,
                     max(lower(status) in ('failed', 'error')) as has_failed,
                     max(lower(status) in ('blocked', 'pending')) as has_pending
              from card_content_blocks {block_condition} group by card_id
            ), card_flags as materialized (
              select c.source_id,
                     case when
                     coalesce(b.has_metadata_fallback, 0)
                     or (coalesce(b.has_failed, 0) and not coalesce(b.has_pending, 0))
                     then 1 else 0 end as degraded
              from cards c left join block_flags b on b.card_id = c.card_id
              where c.{condition}
            )
            select source_id, count(*) as card_count, max(degraded) as degraded
            from card_flags group by source_id""",
        params,
    ).fetchall()
    return (
        {row["source_id"]: row["card_count"] for row in rows},
        {row["source_id"] for row in rows if row["degraded"]},
    )


def load_source_run_facts(
    conn: sqlite3.Connection, source_ids: list[str] | None = None,
    *, now: datetime | None = None,
) -> tuple[dict[str, LatestRunRow], dict[str, RecentRunCounts]]:
    """Return latest runs and 7-day total/success/failed counts, optionally scoped.

    Latest means finished_at DESC, retaining insertion order for ties. Its payload
    stays JSON text. The time window keeps the legacy Python ISO parser, naive
    timestamps as UTC, fallback times and inclusive upper bound of now + 5 min.
    """
    if source_ids == []:
        return {}, {}
    condition = ""
    params = source_ids or []
    if source_ids is not None:
        condition = f"where source_id in ({', '.join('?' for _ in source_ids)})"
    latest = conn.execute(
        f"""with ranked as (
              select rowid as run_rowid, row_number() over (
                partition by source_id order by finished_at desc, rowid
              ) as rank from run_history {condition}
            )
            select r.source_id, r.run_id, r.status, r.finished_at, r.started_at,
                   r.scheduled_for, r.error_summary, r.next_run_at, r.raw_payload
            from ranked join run_history r on r.rowid = ranked.run_rowid
            where ranked.rank = 1""",
        params,
    ).fetchall()
    current = now if now is not None else datetime.now(timezone.utc)

    def within_window(value: str | None) -> bool:
        if not value:
            return False
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return current - timedelta(days=7) <= timestamp <= current + timedelta(minutes=5)

    conn.create_function("source_run_in_window", 1, within_window, deterministic=True)
    try:
        recent = conn.execute(
            f"""select source_id, count(*) as total,
                       sum(status = 'success') as success, sum(status = 'failed') as failed
                from run_history {condition}
                {'and' if condition else 'where'} source_run_in_window(
                  coalesce(nullif(finished_at, ''), nullif(started_at, ''), scheduled_for)
                ) group by source_id""",
            params,
        ).fetchall()
    finally:
        conn.create_function("source_run_in_window", 1, None)
    return (
        {row["source_id"]: cast(LatestRunRow, dict(row)) for row in latest},
        {row["source_id"]: RecentRunCounts(total=row["total"], success=row["success"], failed=row["failed"])
         for row in recent},
    )
