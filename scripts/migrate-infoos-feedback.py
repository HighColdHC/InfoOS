#!/usr/bin/env python3
"""Migrate legacy feedback JSONL into durable InfoOS SQLite user actions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.migrate_feedback import migrate_legacy_feedback
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-dir", default=".")
    args = parser.parse_args(argv)
    initialize_runtime_storage(Path(args.base_dir))
    print(json.dumps(migrate_legacy_feedback(Path(args.base_dir)), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
