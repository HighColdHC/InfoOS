"""Bounded, worker-local lane lookups for repeatedly skipped candidates."""
from __future__ import annotations

import time
from pathlib import Path

from backend.application.tasks.lanes import ExecutionLane, execution_lane_and_site_for
from backend.application.tasks.types import Task


class CandidateLaneCache:
    TTL_SECONDS = 60.0
    MAX_ENTRIES = 5000

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self._entries: dict[tuple[str, str, int], tuple[float, tuple[ExecutionLane, str | None]]] = {}

    def get(self, task: Task) -> tuple[ExecutionLane, str | None]:
        key = (task.task_id, task.input_version, task.attempt)
        now = time.monotonic()
        cached = self._entries.get(key)
        if cached is not None and now - cached[0] < self.TTL_SECONDS:
            return cached[1]
        result = execution_lane_and_site_for(task, self.base_dir)
        if key not in self._entries and len(self._entries) >= self.MAX_ENTRIES:
            self._entries.clear()
        self._entries[key] = (now, result)
        return result
