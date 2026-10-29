#!/usr/bin/env python3
"""Run the B1 migration against an explicitly named SQLite backup copy."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.infrastructure.storage.migrations.runner import migrate_database
from backend.infrastructure.storage.paths import data_dir

MAIN_TABLES = ("sources", "run_history", "raw_items", "cards", "tasks", "task_events")


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _inspect(path: Path) -> dict[str, object]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
        version = int(conn.execute("pragma user_version").fetchone()[0])
        quick_check = [str(row[0]) for row in conn.execute("pragma quick_check")]
        tables = {
            str(row[0]) for row in conn.execute("select name from sqlite_schema where type = 'table'")
        }
        counts = {
            table: int(conn.execute(f"select count(*) from {table}").fetchone()[0])
            for table in MAIN_TABLES if table in tables
        }
    return {"user_version": version, "quick_check": quick_check, "counts": counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Explicit SQLite copy to migrate in place")
    parser.add_argument("--i-know-this-is-a-copy", action="store_true")
    args = parser.parse_args(argv)
    path = args.db.expanduser().resolve()
    if not path.is_file():
        parser.error(f"SQLite copy does not exist: {path}")
    protected = (data_dir().resolve(), Path.home() / "Library/Application Support/InfoOS", Path("/var/lib/infoos"))
    if not args.i_know_this_is_a_copy and any(_within(path, root) for root in protected):
        parser.error("Refusing a default data directory or /var/lib/infoos path; pass --i-know-this-is-a-copy only for a verified copy")

    before = _inspect(path)
    batches: list[dict[str, object]] = []
    started = time.monotonic()
    report = migrate_database(path, progress_callback=batches.append)
    elapsed = time.monotonic() - started
    after = _inspect(path)
    print(json.dumps({
        "db": str(path), "before": before, "after": after,
        "elapsed_seconds": round(elapsed, 3), "batches": batches,
        "migration_report": {
            "before_version": report.before_version,
            "after_version": report.after_version,
            "batches": report.batches,
        },
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if after["quick_check"] == ["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
