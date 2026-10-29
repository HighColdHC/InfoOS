from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from backend.infrastructure.observability.logging import configure_logging, get_logger
from backend.application.tasks.planner import (
    enqueue_fetch_tasks_for_enabled_sources,
    enqueue_pipeline_reconciliation_task,
    enqueue_task_event_archive_task,
    now_utc_iso,
)
from backend.application.tasks.daily_brief_planner import enqueue_daily_brief_build_task
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.database import default_database_path, ensure_database_initialized


def run_scheduler_cycle(
    *,
    base_dir: Path | str,
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Plan durable scheduled work without executing task handlers.

    Scheduler cycles deliberately only expand recurring work into the SQLite
    queue.  ``backend.workers.task_worker`` owns execution and keeps draining
    newly-ready descendants, so a child transition never waits for the next
    scheduler interval.
    """
    base_path = Path(base_dir)
    ensure_database_initialized(default_database_path(base_path))
    configure_logging(base_path)
    logger = get_logger("scheduler.cycle")
    t0 = time.time()
    logger.info("scheduler_cycle_started base_dir=%s max_attempts=%s", base_path, max_attempts)
    current = now_utc_iso()
    planned_fetch_task_ids: list[str] = []
    maintenance_task_ids: list[str] = []
    try:
        planned_fetch_task_ids = enqueue_fetch_tasks_for_enabled_sources(base_path, now=current)
    except Exception as exc:  # noqa: BLE001 - Source planning failures must become explicit partial-cycle results while maintenance planning continues
        source_result = {"status": "failed", "error": str(exc), "planned_task_ids": []}
    else:
        source_result = {
            "status": "planned",
            "planned_task_ids": planned_fetch_task_ids,
            "planned_count": len(planned_fetch_task_ids),
        }
    maintenance_errors: list[str] = []
    for plan_maintenance in (enqueue_pipeline_reconciliation_task, enqueue_task_event_archive_task,
                             enqueue_daily_brief_build_task):
        try:
            task_id = plan_maintenance(base_path, now=current)
            if task_id:
                maintenance_task_ids.append(task_id)
        except Exception as exc:  # noqa: BLE001 - One maintenance planner failure must be reported without preventing independent planners from running
            maintenance_errors.append(f"{plan_maintenance.__name__}: {exc}")
    maintenance_result = {
        "status": "partial_failure" if maintenance_errors else "planned",
        "planned_task_ids": maintenance_task_ids,
        "planned_count": len(maintenance_task_ids),
    }
    if maintenance_errors:
        maintenance_result["errors"] = maintenance_errors

    source_duration_s = round(time.time() - t0, 1)
    queue = _queue_counts(base_path)
    status = (
        "success"
        if source_result["status"] == "planned" and maintenance_result["status"] == "planned"
        else "partial_failure"
    )

    result = {
        "status": status,
        "sources": source_result,
        "maintenance": maintenance_result,
        "scheduled": {"collector_only": True},
        "queue": queue,
        "task_pipeline": {"status": "planned", "reason": "task worker owns execution"},
        "timing": {
            "source_acquisition_s": source_duration_s,
        },
    }
    logger.info(
        "scheduler_cycle_finished status=%s planned_task_count=%s queue_pending=%s",
        status,
        len(planned_fetch_task_ids) + len(maintenance_task_ids),
        queue["pending"],
    )
    return result


def _queue_counts(base_path: Path) -> dict[str, Any]:
    from backend.infrastructure.storage.database import connect_readonly, default_database_path
    with connect_readonly(default_database_path(base_path)) as conn:
        rows = conn.execute("select status, task_type, count(*) n from tasks group by status, task_type").fetchall()
    status_counts = Counter()
    type_counts = Counter()
    for row in rows:
        status_counts[row["status"]] += row["n"]
        type_counts[row["task_type"]] += row["n"]
    return {
        "total": sum(status_counts.values()),
        "pending": status_counts["pending"], "running": status_counts["running"],
        "terminal": sum(status_counts[key] for key in ("success", "failed", "blocked", "skipped")),
        "by_status": dict(sorted(status_counts.items())),
        "by_task_type": dict(sorted(type_counts.items())),
    }



def main(argv: list[str] | None = None) -> int:
    """Module entrypoint: run a single scheduler cycle and print the JSON result.

    Usage:
        python -m backend.workers.scheduler.cycle --base-dir .
    """
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--startup-ready", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.startup_ready:
        initialize_runtime_storage(Path(args.base_dir))
    result = run_scheduler_cycle(
        base_dir=Path(args.base_dir),
        max_attempts=args.max_attempts,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
