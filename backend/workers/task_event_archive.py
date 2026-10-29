"""Query task events across the hot database and monthly archives."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized
from backend.infrastructure.storage.task_event_archive import TaskEventArchiveStore


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    query = subparsers.add_parser("query", help="read matching events from hot and archived databases")
    query.add_argument("--base-dir", type=Path, default=Path("."))
    selector = query.add_mutually_exclusive_group(required=True)
    selector.add_argument("--task-id")
    selector.add_argument("--chain-id")
    args = parser.parse_args(argv)

    ensure_database_initialized(default_database_path(args.base_dir))
    rows = TaskEventArchiveStore(args.base_dir).query(task_id=args.task_id, chain_id=args.chain_id)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
