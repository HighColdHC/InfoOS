"""One reusable browser acquisition lifetime for a continuous lane."""
from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from backend.application.tasks.execution_clock import add_seconds, current_iso
from backend.application.tasks.registry import TaskHandler
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.browser_acquisition_batch import (
    BrowserBatchState, bind_browser_acquisition_batch, browser_acquisition_batch,
)
from backend.infrastructure.observability.logging import get_logger


DEFAULT_BROWSER_BATCH_IDLE_SECONDS = 15.0
DEFAULT_BROWSER_PREPARE_FAILURE_COOLDOWN_SECONDS = 30.0
_BROWSER_READINESS_MAX_DEFERRALS = 3
_BROWSER_READINESS_MAX_TOTAL_DEFERRALS = 10


def _configured_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a non-negative number") from exc
    if value < 0:
        raise RuntimeError(f"{name} must be a non-negative number")
    return value


class BrowserLifetime:
    def __init__(self, base_path: Path, *, clock: Callable[[], float] = time.monotonic):
        self.base_path = base_path
        self.clock = clock
        self.idle_seconds = _configured_seconds(
            "INFOOS_BROWSER_BATCH_IDLE_SECONDS", DEFAULT_BROWSER_BATCH_IDLE_SECONDS,
        )
        self.failure_cooldown_seconds = _configured_seconds(
            "INFOOS_BROWSER_PREPARE_FAILURE_COOLDOWN_SECONDS",
            DEFAULT_BROWSER_PREPARE_FAILURE_COOLDOWN_SECONDS,
        )
        self._prepare_lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._active_task_ids: set[str] = set()
        self._idle_since: float | None = None
        self._state: BrowserBatchState | None = None
        self._context = None
        self._failure_result: dict | None = None
        self._failure_until = 0.0

    def run(self, task: Task, handler: TaskHandler, *, now: str | None = None) -> dict:
        """Prepare lazily, bind attribution to this task, and reuse the session."""
        with self._prepare_lock:
            with self._usage_lock:
                self._active_task_ids.add(task.task_id)
                self._idle_since = None
            state, failure, cached, remaining = self._prepared_state(task)
        if failure is not None:
            if task.task_type == TaskType.AUTH_QR_CAPTURE:
                from backend.application.tasks.handlers.auth_qr import qr_browser_unavailable
                return qr_browser_unavailable(task, self.base_path)
            return _browser_session_not_ready_result(
                task, failure, cached=cached, remaining=remaining, now=now or current_iso(),
            )
        assert state is not None
        with bind_browser_acquisition_batch(state, task=task):
            return handler(task, self.base_path)

    def finish_task(self, task: Task) -> None:
        """Release browser lifetime only after task persistence has finished."""
        with self._usage_lock:
            if task.task_id in self._active_task_ids:
                self._active_task_ids.remove(task.task_id)
                if not self._active_task_ids:
                    self._idle_since = self.clock()

    def _prepared_state(
        self, task: Task,
    ) -> tuple[BrowserBatchState | None, dict | None, bool, float]:
        if self._state is not None:
            self._state.add_task(task)
            return self._state, None, False, 0.0
        remaining = max(0.0, self._failure_until - self.clock())
        if self._failure_result is not None and remaining > 0:
            return None, dict(self._failure_result), True, remaining
        self._failure_result = None
        context = browser_acquisition_batch(
            self.base_path, tasks=[task], bind_context=False,
        )
        try:
            state = context.__enter__()
        except Exception as exc:  # noqa: BLE001 - Browser preparation failures must be logged and cached as explicit readiness failures with cooldown
            get_logger("task_worker.browser").exception(
                "browser_prepare_failed task_id=%s", task.task_id,
            )
            failure = {"status": "error", "error_class": type(exc).__name__}
            self._cache_failure(failure)
            return None, failure, False, max(0.0, self._failure_until - self.clock())
        if state.prepare_result.get("status") != "ready":
            failure = dict(state.prepare_result)
            context.__exit__(None, None, None)
            self._cache_failure(failure)
            return None, failure, False, max(0.0, self._failure_until - self.clock())
        self._state = state
        self._context = context
        return state, None, False, 0.0

    def _cache_failure(self, failure: dict) -> None:
        self._failure_result = dict(failure)
        self._failure_until = self.clock() + self.failure_cooldown_seconds

    def close_if_idle(self, *, force: bool = False) -> bool:
        """Close only after every bound browser handler has left the batch."""
        if not self._prepare_lock.acquire(blocking=False):
            return False
        try:
            with self._usage_lock:
                if self._active_task_ids or self._idle_since is None:
                    return False
                if not force and self.clock() - self._idle_since < self.idle_seconds:
                    return False
            context = self._context
            if context is None:
                return False
            self._context = None
            self._state = None
            context.__exit__(None, None, None)
            return True
        finally:
            self._prepare_lock.release()


def _browser_session_not_ready_result(
    task: Task,
    prepare_result: dict[str, Any],
    *,
    cached: bool,
    remaining: float,
    now: str,
) -> dict[str, Any]:
    deferrals = int(task.artifacts_summary.get("browser_readiness_deferrals") or 0)
    cached_deferrals = int(task.artifacts_summary.get("browser_readiness_cached_deferrals") or 0)
    if cached:
        cached_deferrals += 1
    else:
        deferrals += 1
    result: dict[str, Any] = {
        "status": (
            TaskStatus.PENDING.value
            if (
                deferrals < _BROWSER_READINESS_MAX_DEFERRALS
                and deferrals + cached_deferrals < _BROWSER_READINESS_MAX_TOTAL_DEFERRALS
            )
            else TaskStatus.BLOCKED.value
        ),
        "error": "browser_session_not_ready",
        "artifacts": {
            "browser_session": prepare_result,
            "browser_readiness_deferrals": deferrals,
            "browser_readiness_cached_deferrals": cached_deferrals,
        },
    }
    if result["status"] == TaskStatus.PENDING.value:
        result["next_run_at"] = add_seconds(now, math.ceil(remaining) + 1)
    return result
