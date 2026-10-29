#!/usr/bin/env python3
"""Create an isolated synthetic SQLite copy for task-read benchmarks."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.infrastructure.storage.migrations.runner import migrate_database
from backend.infrastructure.storage.paths import data_dir


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--tasks", type=int, default=200_000)
    parser.add_argument("--events-per-task", type=int, default=4)
    args = parser.parse_args(argv)
    path = args.db.expanduser().resolve()
    if path.exists() or any(_within(path, root) for root in (
        data_dir().resolve(), (Path.home() / "Library/Application Support/InfoOS").resolve(),
        Path("/var/lib/infoos"),
    )):
        parser.error("--db must be a new path outside InfoOS data directories")
    if args.tasks < 1 or args.events_per_task < 0:
        parser.error("task count must be positive and events per task nonnegative")
    migrate_database(path)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """insert into sources(source_id,platform,source_type,identifier,display_name,
                   cadence_hours,connector_status,next_run_at,created_at,updated_at)
               values (?,'rss','feed','https://example.test/feed','Synthetic source',
                       24,'active',?,'2026-01-01','2026-01-01')""",
            [(f"target-{n:04d}", "2026-10-01T00:00:00Z" if n < 100 else None)
             for n in range(4_000)],
        )
        conn.executemany(
            """insert into cards(card_id,source_platform,source_type,source_url,
                   title,content,captured_at,processed_at)
               values (?,'rss','article','https://example.test/card',
                       'Synthetic card','','2026-01-01','2026-01-01')""",
            [(f"card-{n:04d}",) for n in range(4_000)],
        )
        conn.executemany(
            """insert into card_assets(asset_id,card_id,asset_kind,source_url,status,created_at,updated_at)
               values (?,?,'image','https://example.test/asset','ready','2026-01-01','2026-01-01')""",
            [(f"asset-{n:04d}", f"card-{n:04d}") for n in range(4_000)],
        )
        for first in range(0, args.tasks, 2_000):
            stop = min(first + 2_000, args.tasks)
            tasks = []
            events = []
            runs = []
            checkpoints = []
            for n in range(first, stop):
                task_id = f"task-{n:08d}"
                stamp = (start + timedelta(seconds=n // 3)).isoformat(timespec="seconds").replace("+00:00", "Z")
                task_type = "fetch" if n % 10 == 0 else "media_download" if n % 5 == 1 else "card_build"
                target_kind = "source" if task_type == "fetch" else "asset" if task_type == "media_download" else "card"
                target_id = f"asset-{(n // 5) % 4000:04d}" if target_kind == "asset" else f"target-{n % 4000:04d}"
                tasks.append((task_id, task_type, target_kind, target_id,
                              "success", stamp, stamp, stamp))
                if task_type == "fetch" and n % 20 == 0:
                    run_id = f"run-{n:08d}"
                    scheduled = (start + timedelta(seconds=n // 3 + (1 if n % 40 == 0 else 0))).isoformat(timespec="seconds").replace("+00:00", "Z")
                    runs.append((run_id, target_id, scheduled, stamp, stamp, "success", "scheduled"))
                    checkpoints.append((task_id, target_id, run_id, "v1", stamp))
                for event in range(args.events_per_task):
                    events.append((f"event-{n:08d}-{event}", task_id,
                                   "completed" if event == 0 else "progress", stamp))
            conn.executemany(
                """insert into tasks(task_id,task_type,target_kind,target_id,status,
                   created_at,updated_at,finished_at) values (?,?,?,?,?,?,?,?)""", tasks,
            )
            conn.executemany(
                """insert into run_history(run_id,source_id,scheduled_for,started_at,
                   finished_at,status,trigger) values (?,?,?,?,?,?,?)""", runs,
            )
            conn.executemany(
                """insert into acquisition_checkpoints(task_id,source_id,run_id,
                   source_input_version,committed_at) values (?,?,?,?,?)""", checkpoints,
            )
            conn.executemany(
                """insert into task_events(event_id,task_id,event_type,occurred_at)
                   values (?,?,?,?)""", events,
            )
            conn.commit()
    print(f"{path}: {args.tasks} tasks, {args.tasks * args.events_per_task} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
