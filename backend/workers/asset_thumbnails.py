"""Preview or backfill derived attachment thumbnails."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.application.enrichment.media.thumbnails import generate_asset_thumbnail
from backend.infrastructure.storage.card_asset_thumbnails import CardAssetThumbnailRepository
from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized
from backend.infrastructure.storage.migrations.runner import migrate_database


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=Path("."))
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true")
    modes.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args(argv)
    if args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        parser.error("--batch-size and --limit must be positive")
    repo = CardAssetThumbnailRepository(args.base_dir)
    if args.preview:
        ensure_database_initialized(default_database_path(args.base_dir))
        print(json.dumps({"mode": "preview", **repo.summary(retry_failed=args.retry_failed)}))
        return
    migrate_database(default_database_path(args.base_dir))
    counts = {"ready": 0, "failed": 0}
    remaining = args.limit
    after = ""
    while remaining is None or remaining > 0:
        batch = repo.list_pending_batch(
            after_asset_id=after, limit=min(args.batch_size, remaining or args.batch_size),
            retry_failed=args.retry_failed,
        )
        if not batch:
            break
        for asset in batch:
            counts[generate_asset_thumbnail(args.base_dir, asset, repo=repo)] += 1
        after = batch[-1]["asset_id"]
        if remaining is not None:
            remaining -= len(batch)
        print(json.dumps({"progress": {**counts, "last_asset_id": after}}), flush=True)
    print(json.dumps({"mode": "apply", **counts}))


if __name__ == "__main__":
    main()
