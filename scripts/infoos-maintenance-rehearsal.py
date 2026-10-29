#!/usr/bin/env python3
"""Thin adapter for copy-only rehearsal measurements/reporting."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/infrastructure/storage'))
from maintenance_rehearsal import check_receipt, finish_report, stats, unpack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    receipt = sub.add_parser('receipt')
    receipt.add_argument('path', type=Path)
    decompress = sub.add_parser('unpack')
    decompress.add_argument('path', type=Path)
    decompress.add_argument('expected_bytes', type=int)
    measure = sub.add_parser('stats')
    measure.add_argument('path', type=Path)
    measure.add_argument('--baseline', type=Path)
    report = sub.add_parser('report')
    report.add_argument('work', type=Path)
    report.add_argument('path', type=Path)
    report.add_argument('exit_code', type=int)
    report.add_argument('failed_step')
    args = parser.parse_args()
    try:
        if args.action == 'receipt':
            result = check_receipt(args.path)
        elif args.action == 'unpack':
            result = unpack(args.path, args.expected_bytes)
        elif args.action == 'stats':
            result = stats(args.path, args.baseline)
        else:
            result = finish_report(args.work, args.path, args.exit_code, args.failed_step)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports any failure as JSON on stderr with exit code 1
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
