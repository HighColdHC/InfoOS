from __future__ import annotations

from backend.domain.sources.douyin_home_feed import first_session_at, is_douyin_home_feed
from backend.domain.timestamps import format_utc_seconds_z, parse_utc_or_now

import base64
import sqlite3
from datetime import timedelta
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.application.tasks.input_versions import InputSnapshot
from backend.application.tasks.input_versions import source_calibration_input_version
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.application.usecases.source_capability import source_can_enter_fetch_queue


PIPELINE_RECONCILIATION_POLICY_VERSION = "pipeline-reconcile-v5"
TASK_EVENT_ARCHIVE_POLICY_VERSION = "task-event-archive-v1"


def now_utc_iso() -> str:
    return format_utc_seconds_z(parse_utc_or_now(None))


def enqueue_fetch_tasks_for_source(
    base_dir: str | Path,
    source: dict[str, Any],
    *,
    now: str | None = None,
    horizon_hours: float = 0.0,
    plan_followup_fetch: bool = True,
    force_now: bool = False,
    dependency_task_id: str | None = None,
    chain_id: str | None = None,
    chain_root_task_id: str | None = None,
    repo: TaskRepository | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Create pending fetch tasks for an enabled supported source.

    The task table is the scheduler queue. Source rows hold cadence/config;
    by default the planner materializes only work due now. Callers may pass an
    explicit positive horizon to expand future cadence into concrete runs.
    """
    repo = repo or TaskRepository.for_base_dir(base_dir)
    tasks = plan_fetch_tasks_for_source(
        base_dir,
        source,
        now=now,
        horizon_hours=horizon_hours,
        plan_followup_fetch=plan_followup_fetch,
        force_now=force_now,
        dependency_task_id=dependency_task_id,
        chain_id=chain_id,
        chain_root_task_id=chain_root_task_id,
        repo=repo,
    )
    return repo.enqueue_periodic_fetch_singleflight(tasks, conn=conn)


def plan_fetch_tasks_for_source(
    base_dir: str | Path,
    source: dict[str, Any],
    *,
    now: str | None = None,
    horizon_hours: float = 0.0,
    plan_followup_fetch: bool = True,
    force_now: bool = False,
    dependency_task_id: str | None = None,
    chain_id: str | None = None,
    chain_root_task_id: str | None = None,
    repo: TaskRepository | None = None,
) -> list[Task]:
    """Build idempotent FETCH roots without writing them."""
    if not source_can_enter_fetch_queue(source) or not source["enabled"]:
        return []

    cadence_hours = source["cadence_hours"]
    if cadence_hours <= 0:
        return []

    current = parse_utc_or_now(now)
    if force_now:
        first_run = current
    elif source.get("next_run_at"):
        first_run = parse_utc_or_now(source.get("next_run_at"))
    else:
        first_run = current
    if first_run < current:
        first_run = current

    personalized_session = is_douyin_home_feed(source)
    if personalized_session and not force_now:
        first_run = first_session_at(first_run, source["raw_metadata"])
    end = current + timedelta(hours=float(horizon_hours))
    source_id = str(source["source_id"])
    source_input_version = source_calibration_input_version(source)
    repo = repo or TaskRepository.for_base_dir(base_dir)
    tasks: list[Task] = []
    if not force_now and repo.has_circuit_waiting_fetch(source_id):
        return []
    run_at = first_run
    cadence = timedelta(hours=cadence_hours)
    existing = repo.list_by_target(target_kind="source", target_id=source_id, task_type=TaskType.FETCH)
    while run_at <= end:
        scheduled_for = format_utc_seconds_z(run_at)
        if _has_active_periodic_fetch(existing, input_version=source_input_version):
            run_at += cadence
            continue
        task_id = fetch_task_id(source_id, scheduled_for, input_version=source_input_version)
        fetch_chain_id = chain_id or source_run_chain_id(source_id, scheduled_for)
        task = Task(
            task_id=task_id,
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id=source_id,
            next_run_at=scheduled_for,
            max_attempts=4 if source["platform"] == "zsxq" else 3,
            input_version=source_input_version,
            depends_on=(
                (DependencySpec(task_id=dependency_task_id, required_status=TaskStatus.SUCCESS),)
                if dependency_task_id
                else ()
            ),
            artifacts_summary={
                "source_id": source_id,
                "scheduled_for": scheduled_for,
                "cadence_hours": cadence_hours,
                "plan_followup_fetch": plan_followup_fetch,
                "chain_id": fetch_chain_id,
                "chain_root_task_id": chain_root_task_id or task_id,
                "upstream_task_id": dependency_task_id,
                "enforce_source_capability": True,
                "source_input_version": source_input_version,
            },
        )
        tasks.append(task)
        if personalized_session:
            break  # The next random session is based on this FETCH's completion.
        existing.append(task)
        run_at += cadence
    return tasks


def plan_initial_fetch(
    source: dict[str, Any],
    *,
    now: str | None = None,
) -> Task | None:
    """Build the deterministic Initial Fetch primitive without switching lifecycle callers."""
    if not source["enabled"]:
        return None
    source_id = str(source.get("source_id") or "")
    if not source_id:
        return None
    input_version = source_calibration_input_version(source)
    task_id = versioned_task_id(TaskType.FETCH, source_id, input_version)
    chain_id = f"source_initial_fetch_{source_id}_{input_version}"
    return Task(
        task_id=task_id,
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source_id,
        next_run_at=now or now_utc_iso(),
        input_version=input_version,
        max_attempts=4 if source["platform"] == "zsxq" else 3,
        artifacts_summary={
            "source_id": source_id,
            "initial_fetch": True,
            "source_input_version": input_version,
            "chain_id": chain_id,
            "chain_root_task_id": task_id,
            "enforce_source_capability": True,
            "plan_followup_fetch": True,
        },
    )


def enqueue_source_calibration_task(
    base_dir: str | Path,
    source: dict[str, Any],
    *,
    now: str | None = None,
    reason: str = "source_created",
    repo: TaskRepository | None = None,
) -> str | None:
    """Submit one idempotent calibration task for an enabled uncalibrated source."""
    if not source["enabled"]:
        return None
    if source["connector_status"] != "implemented_uncalibrated":
        return None
    source_id = str(source.get("source_id") or "")
    if not source_id:
        return None
    input_version = source_calibration_input_version(source)
    task_id = versioned_task_id(TaskType.SOURCE_CALIBRATE, source_id, input_version)
    chain_id = f"source_calibration_{source_id}_{input_version[:12]}"
    task = Task(
        task_id=task_id,
        task_type=TaskType.SOURCE_CALIBRATE,
        target_kind="source",
        target_id=source_id,
        next_run_at=now or now_utc_iso(),
        input_version=input_version,
        artifacts_summary={
            "source_id": source_id,
            "chain_id": chain_id,
            "chain_root_task_id": task_id,
            "enforce_source_capability": True,
            "calibration_reason": reason,
        },
    )
    (repo or TaskRepository.for_base_dir(base_dir)).enqueue(task)
    return task_id


def enqueue_fetch_tasks_for_enabled_sources(
    base_dir: str | Path,
    *,
    now: str | None = None,
    horizon_hours: float = 0.0,
    plan_followup_fetch: bool = True,
    force_now: bool = False,
) -> list[str]:
    repo = TaskRepository.for_base_dir(base_dir)
    tasks = plan_fetch_tasks_for_enabled_sources(
        base_dir,
        now=now,
        horizon_hours=horizon_hours,
        plan_followup_fetch=plan_followup_fetch,
        force_now=force_now,
        repo=repo,
    )
    return repo.enqueue_periodic_fetch_singleflight(tasks)


def plan_fetch_tasks_for_enabled_sources(
    base_dir: str | Path,
    *,
    now: str | None = None,
    horizon_hours: float = 0.0,
    plan_followup_fetch: bool = True,
    force_now: bool = False,
    repo: TaskRepository | None = None,
) -> list[Task]:
    from backend.infrastructure.storage.repositories import SourceRepository

    task_repo = repo or TaskRepository.for_base_dir(base_dir)
    tasks: list[Task] = []
    for source in SourceRepository(base_dir).list_sources(include_disabled=False):
        tasks.extend(
            plan_fetch_tasks_for_source(
                base_dir,
                source,
                now=now,
                horizon_hours=horizon_hours,
                plan_followup_fetch=plan_followup_fetch,
                force_now=force_now,
                repo=task_repo,
            )
        )
    return tasks


def plan_manual_fetch_task_for_source(
    source: dict[str, Any],
    *,
    now: str | None = None,
    manual_run_id: str | None = None,
) -> Task | None:
    """Build one distinct manual FETCH root without writing it."""
    if not source_can_enter_fetch_queue(source) or not source["enabled"]:
        return None
    source_id = str(source["source_id"])
    source_input_version = source_calibration_input_version(source)
    scheduled_for = format_utc_seconds_z(parse_utc_or_now(now))
    nonce = (manual_run_id or uuid4().hex).replace("-", "")
    task_id = f"{fetch_task_id(source_id, scheduled_for, input_version=source_input_version)}_manual_{nonce}"
    chain_id = f"{source_run_chain_id(source_id, scheduled_for)}_manual_{nonce}"
    return Task(
        task_id=task_id,
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source_id,
        next_run_at=scheduled_for,
        max_attempts=4 if source["platform"] == "zsxq" else 3,
        input_version=source_input_version,
        artifacts_summary={
            "source_id": source_id,
            "scheduled_for": scheduled_for,
            "cadence_hours": source["cadence_hours"],
            "plan_followup_fetch": False,
            "manual_run": True,
            "manual_run_id": nonce,
            "chain_id": chain_id,
            "chain_root_task_id": task_id,
            "enforce_source_capability": True,
            "source_input_version": source_input_version,
        },
    )


def pending_due_fetch_task_ids_for_source(
    base_dir: str | Path,
    source_id: str,
    *,
    now: str,
    limit: int | None = 1,
) -> list[str]:
    from backend.infrastructure.storage.repositories import SourceRepository

    repo = TaskRepository.for_base_dir(base_dir)
    source = SourceRepository(base_dir).get_source(source_id)
    if source is None:
        return []
    source_input_version = source_calibration_input_version(source)
    tasks = repo.list_by_target(
        target_kind="source",
        target_id=source_id,
        task_type=TaskType.FETCH,
    )
    due = [
        task.task_id
        for task in tasks
        if task.status == TaskStatus.PENDING
        and task.input_version == source_input_version
        and (task.next_run_at is None or parse_utc_or_now(task.next_run_at) <= parse_utc_or_now(now))
    ]
    if limit is None:
        return due
    return due[: max(0, int(limit))]


def pending_due_fetch_task_ids_for_enabled_sources(
    base_dir: str | Path,
    *,
    now: str,
    limit_per_source: int | None = 1,
) -> list[str]:
    from backend.infrastructure.storage.repositories import SourceRepository

    task_ids: list[str] = []
    for source in SourceRepository(base_dir).list_sources(include_disabled=False):
        task_ids.extend(
            pending_due_fetch_task_ids_for_source(
                base_dir,
                str(source["source_id"]),
                now=now,
                limit=limit_per_source,
            )
        )
    return task_ids


def fetch_task_id(source_id: str, scheduled_for: str, *, input_version: str | None = None) -> str:
    base = f"fetch_{source_id}_{_task_time_slug(scheduled_for)}"
    return f"{base}_{input_version[:12]}" if input_version else base


def versioned_task_id(task_type: TaskType, target_id: str, input_version: str) -> str:
    """Return a durable task identity for one specific immutable input state."""
    return f"{task_type.value}_{target_id}_{input_version[:12]}"


def enqueue_pipeline_reconciliation_task(
    base_dir: str | Path,
    *,
    now: str,
    repo: TaskRepository | None = None,
) -> str:
    """Schedule at most one durable reconciliation task for a UTC hour."""
    scheduled_hour = parse_utc_or_now(now).replace(minute=0, second=0, microsecond=0)
    hour = format_utc_seconds_z(scheduled_hour)[:13]
    input_version = InputSnapshot(
        target_kind="global",
        target_id="pipeline",
        stage=TaskType.PIPELINE_RECONCILE.value,
        payload={
            "scheduled_hour": hour,
            "policy_version": PIPELINE_RECONCILIATION_POLICY_VERSION,
        },
    ).version()
    task = Task(
        task_id=versioned_task_id(TaskType.PIPELINE_RECONCILE, "pipeline", input_version),
        task_type=TaskType.PIPELINE_RECONCILE,
        target_kind="global",
        target_id="pipeline",
        next_run_at=now,
        input_version=input_version,
        artifacts_summary={
            "scheduled_hour": hour,
            "policy_version": PIPELINE_RECONCILIATION_POLICY_VERSION,
        },
    )
    (repo or TaskRepository.for_base_dir(base_dir)).enqueue(task)
    return task.task_id


def enqueue_task_event_archive_task(
    base_dir: str | Path,
    *,
    now: str,
    repo: TaskRepository | None = None,
) -> str:
    """Schedule one durable task-event archive pass per UTC hour."""
    scheduled_hour = parse_utc_or_now(now).replace(minute=0, second=0, microsecond=0)
    hour = format_utc_seconds_z(scheduled_hour)[:13]
    input_version = InputSnapshot(
        target_kind="global",
        target_id="task_events",
        stage=TaskType.TASK_EVENT_ARCHIVE.value,
        payload={"scheduled_hour": hour, "policy_version": TASK_EVENT_ARCHIVE_POLICY_VERSION},
    ).version()
    task = Task(
        task_id=versioned_task_id(TaskType.TASK_EVENT_ARCHIVE, "task_events", input_version),
        task_type=TaskType.TASK_EVENT_ARCHIVE,
        target_kind="global",
        target_id="task_events",
        next_run_at=now,
        input_version=input_version,
        max_attempts=3,
        artifacts_summary={"scheduled_hour": hour, "policy_version": TASK_EVENT_ARCHIVE_POLICY_VERSION},
    )
    (repo or TaskRepository.for_base_dir(base_dir)).enqueue(task)
    return task.task_id


def source_run_chain_id(source_id: str, scheduled_for: str) -> str:
    return f"source_run_{source_id}_{_task_time_slug(scheduled_for)}"


def card_task_id(
    task_type: TaskType,
    card_id: str,
    *,
    input_version: str | None = None,
) -> str:
    if input_version is not None:
        return versioned_task_id(task_type, card_id, input_version)
    return f"{task_type.value}_{card_id}"


_RAW_ITEM_TARGET_PREFIX = "raw_item:v1:"


def raw_item_target_id(connector_id: str, raw_item_id: str) -> str:
    """Return an opaque, reversible target identity for one persisted raw row."""
    payload = json.dumps([connector_id, raw_item_id], ensure_ascii=False, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")
    return f"{_RAW_ITEM_TARGET_PREFIX}{encoded}"


def parse_raw_item_target_id(target_id: str) -> tuple[str, str] | None:
    """Decode a namespaced raw target, leaving legacy raw IDs untouched."""
    if not target_id.startswith(_RAW_ITEM_TARGET_PREFIX):
        return None
    try:
        payload = target_id.removeprefix(_RAW_ITEM_TARGET_PREFIX)
        connector_id, raw_item_id = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(connector_id, str) or not isinstance(raw_item_id, str):
        return None
    return connector_id, raw_item_id


def raw_item_task_id(
    task_type: TaskType,
    raw_item_id: str,
    *,
    connector_id: str | None = None,
    input_version: str | None = None,
) -> str:
    target_id = raw_item_target_id(connector_id, raw_item_id) if connector_id is not None else raw_item_id
    if input_version is not None:
        return versioned_task_id(task_type, target_id, input_version)
    return f"{task_type.value}_{target_id}"


def asset_task_id(
    task_type: TaskType,
    asset_id: str,
    *,
    input_version: str | None = None,
) -> str:
    if input_version is not None:
        return versioned_task_id(task_type, asset_id, input_version)
    return f"{task_type.value}_{asset_id}"






def _task_time_slug(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace("T", "_").replace("Z", "")


def _has_active_periodic_fetch(
    existing: list[Task],
    *,
    input_version: str,
) -> bool:
    active_statuses = {TaskStatus.PENDING, TaskStatus.RUNNING}
    for task in existing:
        if task.status not in active_statuses or task.input_version != input_version:
            continue
        if not bool(task.artifacts_summary.get("manual_run")):
            return True
    return False
