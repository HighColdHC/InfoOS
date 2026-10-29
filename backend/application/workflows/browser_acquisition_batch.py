"""One browser-session lifecycle for a serial batch of browser fetch tasks."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any, Iterator

from backend.application.tasks.types import Task
from backend.application.usecases.browser_session import (
    cleanup_headless_browser_session,
    prepare_opencli_browser_session,
)
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.opencli.tab_lease import bind_opencli_lease_event_sink


@dataclass
class BrowserBatchState:
    base_path: Path
    tasks: list[Task]
    prepare_result: dict[str, Any]
    should_cleanup_headless: bool
    source_ids: list[str]
    repair_attempts: list[dict[str, Any]] = field(default_factory=list)
    _tasks_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _repair_condition: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _repair_in_progress: bool = field(default=False, repr=False)
    _repair_result: dict[str, Any] | None = field(default=None, repr=False)
    _repair_error: BaseException | None = field(default=None, repr=False)

    def add_task(self, task: Task) -> None:
        """Append task attribution without exposing a half-updated source list."""
        with self._tasks_lock:
            if any(item.task_id == task.task_id for item in self.tasks):
                return
            self.tasks.append(task)
            self.source_ids.append(task.target_id)

    def record_repair(
        self,
        *,
        original_diagnostic: dict[str, Any],
        repair_result: dict[str, Any],
        retried_at: str,
    ) -> None:
        """Retain a repair result without losing an earlier cleanup obligation."""
        self.repair_attempts.append(
            {
                "attempt": len(self.repair_attempts) + 1,
                "original_diagnostic": dict(original_diagnostic),
                "repair_result": dict(repair_result),
                "retried_at": retried_at,
            }
        )
        self.should_cleanup_headless = self.should_cleanup_headless or bool(repair_result.get("browser_headless"))

    def record_event(
        self,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        tasks: list[Task] | None = None,
    ) -> None:
        """Record one compact lifecycle observation per source task in the batch."""
        events = TaskEventRepository(self.base_path)
        with self._tasks_lock:
            selected_tasks = list(tasks) if tasks is not None else list(self.tasks)
            source_ids = list(self.source_ids)
        for task in selected_tasks:
            events.append(
                event_type=event_type,
                task_id=task.task_id,
                chain_id=task.artifacts_summary.get("chain_id"),
                payload={
                    "source_ids": source_ids,
                    "run_id": str(task.artifacts_summary.get("run_id") or task.task_id),
                    **(payload or {}),
                },
            )


_active_browser_batch: ContextVar[BrowserBatchState | None] = ContextVar("active_browser_batch", default=None)


def active_browser_acquisition_batch() -> BrowserBatchState | None:
    """Return the batch active on this execution thread, if any."""
    return _active_browser_batch.get()


@contextmanager
def bind_browser_acquisition_batch_state(state: BrowserBatchState | None) -> Iterator[None]:
    """Bind only batch ownership, preserving the caller's event attribution."""
    token = _active_browser_batch.set(state)
    try:
        yield
    finally:
        _active_browser_batch.reset(token)


@contextmanager
def bind_browser_acquisition_batch(
    state: BrowserBatchState,
    *,
    task: Task | None = None,
) -> Iterator[None]:
    """Bind a prepared batch and its lease-event sink to one worker thread."""
    def record_lease_event(event_type: str, payload: dict[str, str]) -> None:
        state.record_event(event_type, payload=payload, tasks=[task] if task else None)

    with bind_browser_acquisition_batch_state(state):
        with bind_opencli_lease_event_sink(record_lease_event):
            yield


def repair_browser_acquisition_batch(
    state: BrowserBatchState,
    *,
    original_diagnostic: dict[str, Any],
    retried_at: str,
) -> dict[str, Any]:
    """Elect one repair leader and let concurrent failed tasks reuse its result."""
    with state._repair_condition:
        while state._repair_in_progress:
            state._repair_condition.wait()
        if state._repair_result is not None:
            return dict(state._repair_result)
        if state._repair_error is not None:
            raise state._repair_error
        state._repair_in_progress = True

    try:
        state.record_event("browser_repair_started", payload={"retried_at": retried_at})
        repair_result = dict(prepare_opencli_browser_session(wait_seconds=20, probe_x_login=False))
        state.record_repair(
            original_diagnostic=original_diagnostic,
            repair_result=repair_result,
            retried_at=retried_at,
        )
        state.record_event(
            "browser_repair_completed",
            payload={"retried_at": retried_at, "status": repair_result.get("status")},
        )
    except BaseException as exc:
        with state._repair_condition:
            state._repair_error = exc
            state._repair_in_progress = False
            state._repair_condition.notify_all()
        raise
    with state._repair_condition:
        state._repair_result = dict(repair_result)
        state._repair_in_progress = False
        state._repair_condition.notify_all()
    return repair_result


@contextmanager
def browser_acquisition_batch(
    base_dir: Path,
    *,
    tasks: list[Task],
    bind_context: bool = True,
) -> Iterator[BrowserBatchState]:
    """Prepare one browser session and clean up one headless session on exit."""
    state = BrowserBatchState(
        base_path=base_dir,
        tasks=list(tasks),
        prepare_result={},
        should_cleanup_headless=False,
        source_ids=[task.target_id for task in tasks],
    )
    state.record_event("browser_prepare_started")
    prepare_result = dict(prepare_opencli_browser_session(wait_seconds=20, probe_x_login=False))
    state.prepare_result = prepare_result
    state.should_cleanup_headless = bool(prepare_result.get("browser_headless"))
    state.record_event("browser_prepare_completed", payload={"status": prepare_result.get("status")})
    token = _active_browser_batch.set(state) if bind_context else None
    try:
        yield state
    finally:
        if token is not None:
            _active_browser_batch.reset(token)
        from backend.infrastructure.storage.auth_qr import AuthQrRepository
        if state.should_cleanup_headless and not AuthQrRepository(base_dir).has_open_tabs():
            try:
                cleanup_headless_browser_session()
            except Exception as exc:  # noqa: BLE001 - Browser subprocess cleanup must record failure evidence without replacing task results
                state.record_event(
                    "browser_cleanup_failed",
                    payload={"error_class": type(exc).__name__},
                )
            else:
                state.record_event("browser_cleanup_completed")
