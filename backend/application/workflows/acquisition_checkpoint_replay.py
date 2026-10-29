"""Shared complete-checkpoint lookup and legacy artifact recovery."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import LEGACY_INPUT_VERSION, Task
from backend.infrastructure.storage.acquisition_checkpoints import AcquisitionCheckpointRepository


def matching_complete_checkpoint(
    base_dir: Path | str,
    task: Task,
    *,
    source_id: str | None = None,
) -> dict[str, Any] | None:
    source_id = source_id or task.target_id
    expected = str(task.artifacts_summary.get("source_input_version") or task.input_version)
    expected_version = None if expected == LEGACY_INPUT_VERSION else expected
    expected_run_id = str(task.artifacts_summary.get("run_id") or task.task_id)
    repo = AcquisitionCheckpointRepository(base_dir)
    complete = repo.get_complete_matching(
        task_id=task.task_id,
        source_id=source_id,
        expected_source_version=expected_version,
        expected_run_id=expected_run_id,
    )
    if complete is not None:
        return dict(complete["payload"])
    legacy = task.artifacts_summary.get("acquisition_checkpoint")
    if (
        isinstance(legacy, dict)
        and legacy.get("source_id") == source_id
        and repo.upgrade_legacy_if_valid(task_id=task.task_id, payload=legacy)
    ):
        upgraded = repo.get_complete_matching(
            task_id=task.task_id,
            source_id=source_id,
            expected_source_version=expected_version,
            expected_run_id=expected_run_id,
        )
        if upgraded is not None:
            return dict(upgraded["payload"])
    return None
