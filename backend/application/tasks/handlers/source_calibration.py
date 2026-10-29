"""Task handler for durable source capability calibration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus


def handle_source_calibration(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.workflows.source_calibration import StaleSourceCalibrationError, calibrate_source
    from backend.application.workflows.source_initial_fetch import SourceTaskSupersededError

    try:
        artifacts = calibrate_source(base_dir, source_id=task.target_id, task=task)
    except StaleSourceCalibrationError as error:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"source_id": task.target_id, "stale_input_version": task.input_version},
            "error": str(error),
        }
    except SourceTaskSupersededError as error:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"reason": "superseded", "superseded_by": error.winner_task_id},
            "error": "superseded",
        }
    error = artifacts.pop("blocked_error", None)
    if error:
        return {"status": TaskStatus.BLOCKED.value, "artifacts": artifacts, "error": error}
    return {"status": TaskStatus.SUCCESS.value, "artifacts": artifacts}
