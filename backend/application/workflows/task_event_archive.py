"""Application workflow for bounded task-event cold archiving."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from backend.infrastructure.observability.logging import get_logger
from backend.infrastructure.storage.task_event_archive import (
    TaskEventArchiveStore,
    archive_batch_size,
    archive_max_rows,
    archive_max_seconds,
)


logger = get_logger("task_event_archive")


def archive_task_events(
    base_dir: Path | str,
    *,
    now: datetime | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    store = TaskEventArchiveStore(base_dir)
    started = monotonic()
    max_seconds = archive_max_seconds()
    deadline = started + max_seconds
    expired = lambda: monotonic() >= deadline
    archived = 0
    deleted = 0
    files: set[str] = set()
    batches = 0
    source_rowid = 0
    max_delete_transaction_seconds = 0.0

    while archived < archive_max_rows() and not expired():
        batch_limit = min(archive_batch_size(), archive_max_rows() - archived)
        try:
            rows = store.select_eligible(now=current, limit=batch_limit, after_rowid=source_rowid, expired=expired)
            if expired() or not rows:
                break
            source_rowid = max(int(row["archive_source_rowid"]) for row in rows)
            archived_at = current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            paths = store.archive_rows(rows, archived_at=archived_at, expired=expired)
            files.update(path.name for path in paths)
            if expired():
                break
            store.verify_rows(rows, expired=expired)
            if expired():
                break
            removed, delete_seconds = store.delete_verified(rows, now=current, expired=expired)
        except (TimeoutError, sqlite3.OperationalError):
            if expired():
                break
            raise
        archived += len(rows)
        deleted += removed
        batches += 1
        max_delete_transaction_seconds = max(max_delete_transaction_seconds, delete_seconds)
        if removed == 0:
            break

    stats = store.history_stats(now=current, expired=expired)
    remaining_estimate = store.count_eligible(now=current, expired=expired)
    try:
        if store.claim_daily_stats_log(now=current, expired=expired):
            logger.info(
                json.dumps({"event": "task_history_stats", **stats}, ensure_ascii=False, sort_keys=True),
                extra={"area": "task"},
            )
    except sqlite3.OperationalError:
        if not expired():
            raise
    result = {
        "archived_rows": archived,
        "deleted_rows": deleted,
        "archive_files": sorted(files),
        "remaining_eligible_estimate": remaining_estimate,
        "elapsed_seconds": round(monotonic() - started, 6),
        "batches": batches,
        "max_delete_transaction_seconds": round(max_delete_transaction_seconds, 6),
        "history_stats": stats,
    }
    return result
