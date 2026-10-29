from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path
from typing import Any, Callable

from backend.application.tasks.worker import run_due_tasks
from backend.application.tasks.continuous_executor import run_continuous_tasks
from backend.infrastructure.observability.logging import configure_logging, get_logger
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.domain.tasks import TASK_QUEUE_NAMES, TaskType

RunDueTasks = Callable[..., dict[str, Any]]
WaitForStop = Callable[[threading.Event, float], bool]


def run_task_worker_loop(
    *,
    base_dir: Path | str,
    poll_seconds: float = 1.0,
    batch_limit: int = 50,
    max_workers: int | None = None,
    queue_name: str | None = None,
    worker_name: str | None = None,
    task_types: list[TaskType] | None = None,
    stop_event: threading.Event | None = None,
    run_due_tasks_fn: RunDueTasks = run_due_tasks,
    wait_fn: WaitForStop | None = None,
) -> int:
    """Run the shared engine continuously, or poll its synchronous snapshot mode."""
    base_path = Path(base_dir)
    initialize_runtime_storage(base_path)
    stopping = stop_event or threading.Event()
    configure_logging(base_path)
    logger = get_logger("task_worker.loop")
    wait = wait_fn or _wait_for_stop
    previous_handlers = _install_stop_handlers(stopping)
    processed_total = 0
    try:
        if queue_name is not None and task_types is None and batch_limit > 0 and run_due_tasks_fn is run_due_tasks:
            return run_continuous_tasks(
                base_path, queue_name=queue_name, stop_event=stopping,
                poll_seconds=poll_seconds, batch_limit=batch_limit,
                max_workers=max_workers, worker_name=worker_name or queue_name,
            )
        # The unfiltered/diagnostic loop uses the same engine in finite mode
        # through run_due_tasks; it no longer has separate batch admission.
        while not stopping.is_set():
            try:
                run_kwargs: dict[str, Any] = {
                    "limit": batch_limit,
                    "max_workers": max_workers,
                }
                if task_types is not None:
                    run_kwargs["task_types"] = task_types
                if queue_name is not None:
                    run_kwargs["queue_names"] = [queue_name]
                if worker_name is not None or queue_name is not None:
                    run_kwargs["worker_name"] = worker_name or queue_name
                result = run_due_tasks_fn(base_path, **run_kwargs)
                processed = result["processed"]
                processed_total += processed
            except Exception:
                logger.exception("task_worker_cycle_failed base_dir=%s", base_path)
                processed = 0
            if stopping.is_set():
                break
            # A transition may have made descendants ready in this exact
            # batch. Keep draining so they do not wait for the next poll (or
            # scheduler cycle); only an idle queue should sleep.
            if processed > 0:
                continue
            wait(stopping, max(0.0, poll_seconds))
    finally:
        _restore_signal_handlers(previous_handlers)
    return processed_total


def _wait_for_stop(stop_event: threading.Event, seconds: float) -> bool:
    return stop_event.wait(seconds)


def _install_stop_handlers(stop_event: threading.Event) -> dict[int, Any]:
    if threading.current_thread() is not threading.main_thread():
        return {}

    previous: dict[int, Any] = {}

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.signal(signum, request_stop)
    return previous


def _restore_signal_handlers(previous_handlers: dict[int, Any]) -> None:
    for signum, handler in previous_handlers.items():
        signal.signal(signum, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the durable InfoOS task worker.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--batch-limit", type=int, default=50)
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--queue", choices=TASK_QUEUE_NAMES)
    parser.add_argument("--worker-name")
    parser.add_argument("--task-type", choices=[task.value for task in TaskType], action="append")
    args = parser.parse_args(argv)
    run_task_worker_loop(
        base_dir=Path(args.base_dir),
        poll_seconds=args.poll_seconds,
        batch_limit=args.batch_limit,
        max_workers=args.max_workers,
        queue_name=args.queue,
        worker_name=args.worker_name,
        task_types=[TaskType(value) for value in args.task_type] if args.task_type is not None else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
