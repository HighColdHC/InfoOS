#!/usr/bin/env python3
"""Read-only SQLite probes for task history; optionally migrate an explicit copy."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.infrastructure.storage.migrations.runner import migrate_database
from backend.infrastructure.storage.paths import data_dir
from backend.infrastructure.storage.source_run_list_queries import load_source_run_catalog_page


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _probes(conn: sqlite3.Connection) -> dict[str, tuple[str, tuple[object, ...]]]:
    card = conn.execute("select card_id from card_assets limit 1").fetchone()
    card_id = str(card[0]) if card else "__no_matching_card__"
    return {
        "task_recent": (
            "select * from tasks order by created_at desc, task_id limit 100", (),
        ),
        "source_fetch_base_legacy": (
            """select t.task_id,
                      coalesce(r.scheduled_for, json_extract(t.artifacts_summary,'$.scheduled_for'),
                               t.next_run_at,t.started_at,t.created_at) as scheduled_at,
                      (select count(*) from task_events e where e.task_id=t.task_id
                       and e.event_type in ('retry_scheduled','completed','failed','blocked','skipped'))
                 from tasks t
                 left join acquisition_checkpoints c on c.task_id=t.task_id
                 left join run_history r on r.run_id=c.run_id
                 where t.task_type='fetch'
                 order by julianday(scheduled_at) desc,t.task_id limit 50""", (),
        ),
        "source_fetch_candidate": (
            """select t.task_id,
                      coalesce(r.scheduled_for, json_extract(t.artifacts_summary,'$.scheduled_for'),
                               t.next_run_at,t.started_at,t.created_at) as scheduled_at
                 from tasks t
                 left join acquisition_checkpoints c on c.task_id=t.task_id
                 left join run_history r on r.run_id=c.run_id
                 where t.task_type='fetch'
                 order by julianday(scheduled_at) desc,t.task_id limit 50""", (),
        ),
        "card_task_ids_legacy": (
            """select task_id from tasks where target_kind='card' and target_id=?
                 union all select t.task_id from tasks t join card_assets a on a.asset_id=t.target_id
                 where t.target_kind='asset' and a.card_id=?""", (card_id, card_id),
        ),
        "card_task_ids": (
            """select task_id from tasks where target_kind='card' and target_id=?
                 union all select t.task_id from card_assets a indexed by idx_card_assets_card
                 join tasks t indexed by idx_tasks_target
                   on t.target_kind='asset' and t.target_id=a.asset_id
                 where a.card_id=?""", (card_id, card_id),
        ),
    }


def _measure(conn: sqlite3.Connection, sql: str, params: tuple[object, ...], repeats: int) -> dict[str, object]:
    plan = [str(row[3]) for row in conn.execute("explain query plan " + sql, params)]
    timings: list[float] = []
    count = 0
    for _ in range(repeats + 1):
        started = time.perf_counter()
        count = len(conn.execute(sql, params).fetchall())
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "rows": count,
        "first_ms": round(timings[0], 3),
        "hot_median_ms": round(statistics.median(timings[1:]), 3),
        "explain": plan,
    }


def _measure_catalog(conn: sqlite3.Connection, repeats: int, offset: int) -> dict[str, object]:
    """Time the production catalog query using the same read-only connection."""
    current = datetime.now(timezone.utc)
    now = current.isoformat(timespec="seconds").replace("+00:00", "Z")
    day_ago = (current - timedelta(hours=24)).isoformat(timespec="seconds").replace("+00:00", "Z")
    traced_sql: list[str] = []
    timings: list[float] = []
    result: dict[str, object] = {}

    def trace(statement: str) -> None:
        if len(traced_sql) < 16 and statement.lstrip().lower().startswith(("select", "with")):
            traced_sql.append(statement)

    conn.row_factory = sqlite3.Row
    conn.execute("begin")
    conn.set_trace_callback(trace)
    try:
        for attempt in range(repeats + 1):
            started = time.perf_counter()
            result = load_source_run_catalog_page(
                ".", now=now, last_24h_start=day_ago, query=None, platform=None,
                time_from=None, time_to=None, execution_scope="all", status=None,
                limit=50, offset=offset, conn=conn,
            )
            timings.append((time.perf_counter() - started) * 1000)
            if attempt == 0:
                conn.set_trace_callback(None)
    finally:
        conn.set_trace_callback(None)
        conn.rollback()
    plans = [[str(row[3]) for row in conn.execute("explain query plan " + sql)]
             for sql in traced_sql]
    highlights = [f"query {number}: {item}"
                  for number, plan in enumerate(plans, start=1) for item in plan
                  if any(term in item for term in (
                      "SCAN t", "SEARCH t", "SCAN r", "SEARCH r", "task_events",
                      "TEMP B-TREE", "MATERIALIZE catalog", "candidate_scoped",
                      "idx_tasks_", "idx_run_history_", "idx_acquisition_",
                  ))]
    return {
        "rows": len(result.get("rows", [])), "offset": offset,
        "total": result.get("total"),
        "counts_exact": result.get("counts_exact"),
        "first_ms": round(timings[0], 3),
        "hot_median_ms": round(statistics.median(timings[1:]), 3),
        "explain_node_count": sum(map(len, plans)), "explain": highlights[:40],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Explicit SQLite backup copy")
    parser.add_argument("--migrate-copy", action="store_true", help="Run v0003 migration on this copy first")
    parser.add_argument("--i-know-this-is-a-copy", action="store_true")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--catalog-offset", type=int, default=0,
                        help="Source Run catalog offset for deep-page profiling")
    args = parser.parse_args(argv)
    path = args.db.expanduser().resolve()
    if not path.is_file():
        parser.error(f"SQLite copy does not exist: {path}")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.catalog_offset < 0:
        parser.error("--catalog-offset must be nonnegative")
    protected = (
        data_dir().resolve(),
        (Path.home() / "Library/Application Support/InfoOS").resolve(),
        Path("/var/lib/infoos"),
    )
    if not args.i_know_this_is_a_copy and any(_within(path, root) for root in protected):
        parser.error("Refusing a default data directory or /var/lib/infoos path; override only for a verified copy")
    migration = None
    if args.migrate_copy:
        report = migrate_database(path)
        migration = {"before_version": report.before_version, "after_version": report.after_version}
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
        version = int(conn.execute("pragma user_version").fetchone()[0])
        probes = {name: _measure(conn, sql, params, args.repeats)
                  for name, (sql, params) in _probes(conn).items()}
        probes["source_run_catalog_page"] = _measure_catalog(conn, args.repeats, args.catalog_offset)
    print(json.dumps({"db": str(path), "user_version": version, "migration": migration,
                      "probes": probes}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
