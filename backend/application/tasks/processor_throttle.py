"""Fetch throttle decisions used by task execution and admission."""

from __future__ import annotations

from pathlib import Path

from backend.application.tasks.types import Task, TaskType


def fetch_throttle_decision(task: Task, base_path: Path, *, now: str | None):
    return _fetch_throttle_decision(task, base_path, now=now, reserve=True)


def peek_fetch_throttle_decision(task: Task, base_path: Path, *, now: str | None):
    """Mirror execution eligibility while leaving shared throttle state untouched."""
    return _fetch_throttle_decision(task, base_path, now=now, reserve=False)


def _fetch_throttle_decision(
    task: Task,
    base_path: Path,
    *,
    now: str | None,
    reserve: bool,
):
    from backend.application.tasks.fetch_rate_limit import FetchThrottleDecision

    if task.task_type != TaskType.FETCH or task.target_kind != "source":
        return FetchThrottleDecision(allowed=True)
    from backend.application.tasks.fetch_rate_limit import peek_fetch_slot, reserve_fetch_slot
    from backend.application.tasks.types import LEGACY_INPUT_VERSION
    from backend.infrastructure.storage.repositories import SourceRepository
    from backend.application.workflows.acquisition_checkpoint_replay import matching_complete_checkpoint

    if matching_complete_checkpoint(base_path, task) is not None:
        return FetchThrottleDecision(allowed=True)
    source = SourceRepository(base_path).get_source(task.target_id)
    declared_version = str(task.artifacts_summary.get("source_input_version") or task.input_version)
    if (
        source is not None
        and declared_version != LEGACY_INPUT_VERSION
        and declared_version != str(source.get("calibration_input_version") or "")
    ):
        return FetchThrottleDecision(allowed=True)

    decide = reserve_fetch_slot if reserve else peek_fetch_slot
    return decide(base_path, source_id=task.target_id, now=now)
