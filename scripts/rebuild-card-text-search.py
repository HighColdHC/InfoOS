#!/usr/bin/env python3
"""Offline VACUUM -> rebuild FTS -> validate; default is a read-only preview."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.infrastructure.storage.card_text_maintenance import maintain_card_text_search


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='Execute only after stopping services and verifying backup')
    parser.add_argument('--keyword', action='append', help='Repeat for 3 representative literal FTS terms (3+ characters)')
    parser.add_argument('--rebuild-only', action='store_true', help='Repair after an interrupted maintenance; skip VACUUM')
    args = parser.parse_args()
    try:
        result = maintain_card_text_search(args.db, apply=args.apply, keywords=args.keyword,
                                          rebuild_only=args.rebuild_only)
    except Exception as exc:
        print(json.dumps({'error': str(exc), 'instruction': 'Keep services stopped after an apply failure; '
                          'repair with --rebuild-only --apply or restore the verified backup.'}), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
