"""Durable source capability calibration before regular acquisition."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.application.tasks.planner import enqueue_fetch_tasks_for_source, now_utc_iso
from backend.application.tasks.input_versions import source_calibration_input_version
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.workflows.acquisition_failures import (
    AcquisitionFailureError,
    classify_acquisition_failure,
)
from backend.application.workflows.fetch_support import build_connector as _build_connector
from backend.domain.models.raw_item import RawItem
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.infrastructure.storage.repositories import SourceCalibrationRepository, SourceRepository


class StaleSourceCalibrationError(RuntimeError):
    """The source changed after this immutable calibration task was planned."""


def calibrate_source(base_dir: Path, *, source_id: str, task: Task) -> dict[str, Any]:
    """Probe one item, persist evidence, then submit the first real fetch."""
    source_repo = SourceRepository(base_dir)
    evidence_repo = SourceCalibrationRepository(base_dir)
    source = source_repo.get_source(source_id)
    if source is None:
        return _blocked_artifacts(source_id, "source_missing")
    if source_calibration_input_version(source) != task.input_version:
        raise StaleSourceCalibrationError("source_configuration_changed")
    from backend.application.workflows.source_initial_fetch import ensure_task_wins_source_arbitration

    ensure_task_wins_source_arbitration(base_dir, task)
    existing = evidence_repo.get_by_task_id(task.task_id)
    if existing is not None and existing["result"] == "success":
        return _resume_from_evidence(base_dir, source, task, existing)
    if existing is not None and existing["attempt"] >= task.attempt:
        return _resume_from_evidence(base_dir, source, task, existing)

    try:
        probe = probe_source_for_calibration(base_dir, source, limit=1)
    except Exception as error:
        failure = classify_acquisition_failure(source=source, result=None, error=error)
        if failure is None:
            raise
        if not failure.retryable:
            _record_evidence(
                source_repo,
                source,
                task,
                result="failed",
                failure_class=failure.reason.value,
                error_summary=failure.message,
                raw_payload={"diagnostic": failure.diagnostic, "user_action": failure.user_action},
            )
        raise AcquisitionFailureError(failure) from error

    if probe["visible_count"] <= 0:
        evidence = _record_evidence(
            source_repo,
            source,
            task,
            result="failed",
            failure_class="empty_calibration",
            error_summary="calibration_returned_no_items",
            raw_payload={"probe": _probe_counts(probe)},
        )
        return _blocked_artifacts(source_id, "calibration_returned_no_items", evidence=evidence)

    fetch_task_ids: list[str] = []
    promoted = {**source, "connector_status": "supported"}
    calibrated_at = now_utc_iso()
    evidence = _record_evidence(
        source_repo,
        source,
        task,
        result="success",
        visible_count=probe["visible_count"],
        sample_count=probe["sample_count"],
        raw_payload={"probe": _probe_counts(probe)},
        connector_status="supported",
        calibrated_at=calibrated_at,
        on_recorded=lambda conn: fetch_task_ids.extend(
            _enqueue_first_fetch(base_dir, promoted, task, {"calibrated_at": calibrated_at}, conn=conn)
        ),
    )
    return {
        "source_id": source_id,
        "calibration_id": evidence["calibration_id"],
        "calibration_result": "success",
        "connector_status": "supported",
        "visible_count": evidence["visible_count"],
        "sample_count": evidence["sample_count"],
        "fetch_task_ids": fetch_task_ids,
    }


def probe_source_for_calibration(base_dir: Path, source: dict[str, Any], *, limit: int = 1) -> dict[str, Any]:
    """Run a disposable connector probe without committing fetched content."""
    connector_config = database_source_to_connector_config(
        {**source, "max_items_per_run": max(1, int(limit))}
    )
    if connector_config.get("type") == "opencli":
        connector_config = {**connector_config, "keep_alive": False}
    connector = _build_connector(base_dir, connector_config)
    result = connector.fetch_new_items(since_cursor=None)
    items = list(result.items[: max(1, int(limit))])
    return {
        "visible_count": int(result.visible_count),
        "sample_count": len(items),
        "samples": [_sample(item) for item in items],
    }


def _record_evidence(
    source_repo: SourceRepository,
    source: dict[str, Any],
    task: Task,
    *,
    result: str,
    visible_count: int = 0,
    sample_count: int = 0,
    failure_class: str | None = None,
    error_summary: str | None = None,
    raw_payload: dict[str, Any] | None = None,
    connector_status: str | None = None,
    calibrated_at: str | None = None,
    on_recorded: Callable[[sqlite3.Connection], None] | None = None,
) -> dict[str, Any]:
    completed_at = calibrated_at or now_utc_iso()
    calibration = {
        "calibration_id": _calibration_id(task),
        "source_id": str(source["source_id"]),
        "platform": source["platform"],
        "source_type": source["source_type"],
        "identifier": source["identifier"],
        "result": result,
        "visible_count": visible_count,
        "sample_count": sample_count,
        "failure_class": failure_class,
        "error_summary": error_summary,
        "task_id": task.task_id,
        "attempt": task.attempt,
        "calibrated_at": completed_at,
        "raw_payload": raw_payload or {},
    }
    try:
        recorded = source_repo.record_calibration_result(
            calibration=calibration,
            connector_status=connector_status or source["connector_status"],
            expected_source=source,
            on_recorded=on_recorded,
        )
        if not recorded:
            raise StaleSourceCalibrationError("source_configuration_changed")
    except sqlite3.IntegrityError:
        existing = SourceCalibrationRepository(source_repo.base_dir).get_by_task_id(task.task_id)
        if existing is None:
            raise
        return existing
    return calibration


def _resume_from_evidence(
    base_dir: Path,
    source: dict[str, Any],
    task: Task,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    if evidence["result"] != "success":
        return _blocked_artifacts(
            str(source["source_id"]),
            str(evidence.get("error_summary") or "calibration_failed"),
            evidence=evidence,
        )
    fetch_task_ids = _enqueue_first_fetch(base_dir, source, task, evidence)
    return {
        "source_id": str(source["source_id"]),
        "calibration_id": evidence["calibration_id"],
        "calibration_result": "success",
        "connector_status": source["connector_status"],
        "visible_count": evidence["visible_count"],
        "sample_count": evidence["sample_count"],
        "fetch_task_ids": fetch_task_ids,
        "replayed": True,
    }


def _enqueue_first_fetch(
    base_dir: Path,
    source: dict[str, Any],
    task: Task,
    evidence: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    scheduled_for = evidence["calibrated_at"]
    created = enqueue_fetch_tasks_for_source(
        base_dir,
        source,
        now=scheduled_for,
        horizon_hours=0,
        plan_followup_fetch=True,
        force_now=True,
        dependency_task_id=task.task_id,
        chain_id=str(task.artifacts_summary.get("chain_id") or "") or None,
        chain_root_task_id=str(task.artifacts_summary.get("chain_root_task_id") or task.task_id),
        conn=conn,
    )
    if created:
        return created
    existing = TaskRepository.for_base_dir(base_dir).list_by_target(
        target_kind="source",
        target_id=str(source["source_id"]),
        task_type=TaskType.FETCH,
    )
    current_version = str(source.get("calibration_input_version") or source_calibration_input_version(source))
    current = [candidate for candidate in existing if candidate.input_version == current_version]
    exact = [candidate.task_id for candidate in current if candidate.next_run_at == scheduled_for]
    if exact:
        return exact
    return [
        candidate.task_id
        for candidate in current
        if candidate.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
    ][:1]


def _blocked_artifacts(source_id: str, error: str, *, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "calibration_id": (evidence or {}).get("calibration_id"),
        "calibration_result": "failed",
        "connector_status": "implemented_uncalibrated",
        "fetch_task_ids": [],
        "blocked_error": error,
    }


def _sample(item: RawItem) -> dict[str, Any]:
    return {
        "raw_item_id": item.raw_item_id,
        "title": item.raw_metadata.get("title") or (item.raw_text or "").splitlines()[0][:120],
        "source_url": item.source_url,
    }


def _probe_counts(probe: dict[str, Any]) -> dict[str, int]:
    return {
        "visible_count": probe["visible_count"],
        "sample_count": probe["sample_count"],
    }


def _calibration_id(task: Task) -> str:
    suffix = f"_attempt_{task.attempt}" if int(task.attempt) > 1 else ""
    return f"cal_{task.task_id}{suffix}"
