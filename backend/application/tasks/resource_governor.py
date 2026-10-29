"""Global resource admission for memory-sensitive durable tasks."""
from __future__ import annotations

from dataclasses import dataclass
import os

from backend.application.tasks.admission import ResourceRequest
from backend.application.tasks.types import Task, TaskType
from backend.infrastructure.runtime.resource_pressure import (
    RuntimePressureSnapshot,
    collect_runtime_pressure,
)


_MEDIA_COMPUTE_TASKS = frozenset(
    {
        TaskType.VIDEO_EXTRACT_AUDIO,
        TaskType.VIDEO_TRANSCRIBE,
    }
)


@dataclass(frozen=True)
class TaskResourcePlan:
    allowed: bool
    resource_request: ResourceRequest | None = None
    defer_reason: str | None = None


def resource_plan_for(
    task: Task,
    *,
    snapshot: RuntimePressureSnapshot | None = None,
) -> TaskResourcePlan:
    """Return the complete resource decision for one task.

    Callers only need to honor ``allowed`` and atomically claim the optional
    resource request. Pressure thresholds and pooled-slot details stay behind
    this interface.
    """
    if task.task_type not in _MEDIA_COMPUTE_TASKS:
        return TaskResourcePlan(allowed=True)

    pressure = snapshot or collect_runtime_pressure()
    defer_reason = _pressure_defer_reason(pressure)
    if defer_reason is not None:
        return TaskResourcePlan(allowed=False, defer_reason=defer_reason)

    slots = _positive_int("INFOOS_MEDIA_COMPUTE_SLOTS", 2)
    return TaskResourcePlan(
        allowed=True,
        resource_request=ResourceRequest.pooled("media_compute", slots),
    )


def _pressure_defer_reason(snapshot: RuntimePressureSnapshot) -> str | None:
    full_threshold = _non_negative_float(
        "INFOOS_RESOURCE_PRESSURE_FULL_AVG10",
        1.0,
    )
    if (
        snapshot.memory_full_avg10 is not None
        and snapshot.memory_full_avg10 >= full_threshold
    ):
        return "memory_full_pressure"

    min_available = _positive_int(
        "INFOOS_RESOURCE_MIN_AVAILABLE_MIB",
        4096,
    ) * 1024**2
    if (
        snapshot.mem_available_bytes is not None
        and snapshot.mem_available_bytes < min_available
    ):
        return "low_available_memory"

    soft_limit = _positive_int(
        "INFOOS_MEMORY_SOFT_LIMIT_MIB",
        10 * 1024,
    ) * 1024**2
    if (
        snapshot.cgroup_current_bytes is not None
        and snapshot.cgroup_current_bytes >= soft_limit
    ):
        return "container_memory_soft_limit"

    if (
        snapshot.cgroup_high_bytes is not None
        and snapshot.cgroup_current_bytes is not None
        and snapshot.cgroup_current_bytes >= snapshot.cgroup_high_bytes
    ):
        return "container_memory_high"
    return None


def _positive_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return parsed


def _non_negative_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if parsed < 0:
        raise RuntimeError(f"{name} must be non-negative")
    return parsed
