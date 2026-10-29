"""Application compatibility imports for canonical domain task values."""
from backend.domain.tasks import (
    LEGACY_INPUT_VERSION,
    DependencySpec,
    Task,
    TaskQueue,
    TaskStatus,
    TaskType,
    TASK_QUEUE_NAMES,
    new_task_completion_token,
)

__all__ = [
    "LEGACY_INPUT_VERSION",
    "DependencySpec",
    "Task",
    "TaskQueue",
    "TaskStatus",
    "TaskType",
    "TASK_QUEUE_NAMES",
    "new_task_completion_token",
]
