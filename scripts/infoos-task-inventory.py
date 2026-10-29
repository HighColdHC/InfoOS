#!/usr/bin/env python3
"""Read-only review-04 inventory; candidates are a preview, never permission to delete."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

# Import only the standalone tool, avoiding storage package initialization.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/infrastructure/storage'))
from task_inventory import inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('--archive-dir', type=Path)
    parser.add_argument('--now', help='ISO8601 time with timezone; default current UTC')
    parser.add_argument('--max-seconds', type=float, default=240)
    args = parser.parse_args()
    try:
        if args.archive_dir is not None and not args.archive_dir.is_dir():
            raise ValueError('--archive-dir must be an existing readable directory')
        if args.max_seconds <= 0:
            raise ValueError('--max-seconds must be positive')
        now = datetime.now(timezone.utc) if not args.now else datetime.fromisoformat(args.now.replace('Z', '+00:00'))
        if now.tzinfo is None:
            raise ValueError('--now requires timezone')
        result = inventory(args.database, now.astimezone(timezone.utc), args.max_seconds, args.archive_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (sqlite3.Error, ValueError, TimeoutError, OSError) as exc:
        print(json.dumps({'error': str(exc), 'complete': False, 'candidates': None}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
