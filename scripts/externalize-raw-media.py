#!/usr/bin/env python3
"""Preview inline raw/card media; apply only in an approved offline maintenance window."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.infrastructure.storage.raw_media_maintenance import maintain_raw_media


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--services-stopped", action="store_true", help="Confirm all readers/writers are stopped")
    parser.add_argument("--backup-verified", action="store_true", help="Confirm a consistent, restorable backup was verified")
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    try:
        result = maintain_raw_media(args.db, apply=args.apply, services_stopped=args.services_stopped,
                                    backup_verified=args.backup_verified, batch_size=args.batch_size)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "instruction": "After apply failure keep services stopped; "
                          "inspect committed pages/files, rerun or restore verified backup."}), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
