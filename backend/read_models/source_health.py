"""Shared source readiness rules for persisted maintenance projections."""

from __future__ import annotations

from typing import NamedTuple, TypedDict

from backend.infrastructure.storage.sources import SourceRow


class ReadinessDiagnostic(TypedDict):
    category: str


class SourceReadiness(TypedDict):
    status: str
    issues: list[str]
    diagnostic: ReadinessDiagnostic | None


class CalibrationTask(NamedTuple):
    """The current-version initial FETCH or SOURCE_CALIBRATE task of a source."""

    task_id: str
    status: str
    attempt: int


def _derived_readiness_status(
    source: SourceRow,
    calibration: CalibrationTask | None,
    *,
    metadata_fallback: bool,
    base_readiness: SourceReadiness,
) -> str:
    if not source["enabled"]:
        return "disabled"
    status = calibration.status if calibration else ""
    if source["connector_status"] == "implemented_uncalibrated":
        if status == "running":
            return "initial_fetch_running"
        if status == "pending" and calibration and calibration.attempt > 1:
            return "initial_fetch_retrying"
        if status in {"blocked", "failed"}:
            return "needs_action"
        return "awaiting_initial_fetch"
    if source["connector_status"] in {"planned", "unsupported"}:
        return "needs_action"
    if base_readiness["status"] == "blocked":
        return "needs_action"
    if status in {"blocked", "failed"}:
        return "needs_action"
    if source["connector_status"] == "partial" or metadata_fallback:
        return "degraded"
    return "ready"
