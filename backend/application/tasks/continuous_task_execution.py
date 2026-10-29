"""One claimed task's heartbeat, circuit admission, and execution."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from backend.application.tasks.admission import (
    ResourceLease, new_resource_owner_token, platform_scope_for_task,
)
from backend.application.tasks.continuous_browser import BrowserLifetime
from backend.application.tasks.execution_clock import add_seconds, current_iso
from backend.application.tasks.lanes import ExecutionLane
from backend.application.tasks.probe_heartbeat import ProbeHeartbeat
from backend.application.tasks.processor_execution import heartbeat_task, process_task
from backend.application.tasks.registry import TaskHandler, handler_for
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.application.workflows.acquisition_checkpoint_replay import matching_complete_checkpoint
from backend.infrastructure.observability.logging import get_logger
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.runtime_resource_leases import RuntimeResourceLeaseRepository


def complete_checkpoint(base_path: Path, task: Task) -> bool:
    return (task.task_type is TaskType.FETCH
            and matching_complete_checkpoint(base_path, task) is not None)


def _execute_claimed(
    task: Task, *, base_path: Path, worker_id: str, lease_seconds: int,
    resource: ResourceLease | None, handlers: dict[TaskType, TaskHandler] | None,
    lane: ExecutionLane | None = None,
    browser: BrowserLifetime | None = None,
    replay_selected: bool = False,
    now: str | None = None,
    stop_event: threading.Event | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Protect a claim, admit it, then bind browser state only if needed."""
    repo = TaskRepository.for_base_dir(base_path)
    circuits = RuntimeCircuitRepository(base_path)
    browser_lane = lane is ExecutionLane.BROWSER_ACQUISITION
    stopped, ownership_lost = threading.Event(), threading.Event()
    probe = ProbeHeartbeat(base_path)
    heartbeat = threading.Thread(
        target=heartbeat_task,
        args=(repo, task.task_id, worker_id, stopped, lease_seconds,
              resource, base_path, ownership_lost, probe),
        daemon=True,
    )
    heartbeat.start()
    platform: str | None = None
    owner_token: str | None = None
    acquired_probe = False
    processor_completed = False
    try:
        replay_now = browser_lane and complete_checkpoint(base_path, task)
        if replay_now and resource is not None:
            stopped.set()
            heartbeat.join()
            if ownership_lost.is_set():
                return None, "ownership_lost"
            released = RuntimeResourceLeaseRepository(base_path).release(
                resource.resource_kind, resource.resource_key,
                owner_token=resource.owner_token,
            )
            if not released:
                return None, "ownership_lost"
            resource = None
            stopped, ownership_lost = threading.Event(), threading.Event()
            heartbeat = threading.Thread(
                target=heartbeat_task,
                args=(repo, task.task_id, worker_id, stopped, lease_seconds,
                      None, base_path, ownership_lost, probe),
                daemon=True,
            )
            heartbeat.start()
        if browser_lane and replay_selected and not replay_now:
            returned = repo.defer(
                task.task_id, worker_id=worker_id,
                next_run_at=add_seconds(now or current_iso(), 1),
                error_reason="checkpoint_changed_before_execution",
            )
            return None, "checkpoint_changed" if returned else "ownership_lost"
        platform = None if replay_now else platform_scope_for_task(task, base_path)
        if platform is not None:
            owner_token = new_resource_owner_token()
            admission_now = now or current_iso()
            admission = circuits.admit(
                "platform", platform, owner_token=owner_token, now=admission_now,
                task_id=task.task_id,
                chain_id=task.artifacts_summary.get("chain_id"),
            )
            if not admission.allowed:
                retry_at = admission.retry_at or add_seconds(admission_now, 300)
                returned = repo.defer_owned_for_circuit(
                    task, worker_id=worker_id, next_run_at=retry_at,
                    occurred_at=admission_now, scope_kind="platform",
                    scope_key=platform, resource=resource,
                )
                return None, "circuit_deferred" if returned else "ownership_lost"
            if admission.probe:
                acquired_probe = True
                probe.bind("platform", platform, owner_token)
        if ownership_lost.is_set():
            return None, "ownership_lost"
        effective_handlers = handlers
        if browser_lane and not replay_now:
            effective_handlers = dict(handlers or {})
            effective_handlers[task.task_type] = (handlers or {}).get(task.task_type) or handler_for(task.task_type)

        def browser_runner(current_task, _base, handler):
            assert browser is not None
            return browser.run(current_task, handler, now=now)

        result = process_task(
            task, base_path, now=now, handlers=effective_handlers, worker_id=worker_id,
            lease_seconds=lease_seconds, resource_lease=resource,
            platform_scope=platform, platform_circuit_owner=owner_token,
            platform_circuit_probe=acquired_probe, probe_heartbeat=probe,
            handler_runner=browser_runner if browser_lane and not replay_now else None,
            external_heartbeat=(stopped, ownership_lost, heartbeat),
            stop_event=stop_event,
        )
        processor_completed = True
        return result, None
    except Exception:  # noqa: BLE001 - Claimed-task preflight failures must be logged and deferred so the worker lane survives
        get_logger("task_worker.loop").exception(
            "task_worker_claimed_preflight_failed task_id=%s", task.task_id,
        )
        repo.defer(
            task.task_id, worker_id=worker_id,
            next_run_at=add_seconds(now or current_iso(), 1),
            error_reason="admission_failed",
        )
        return None, "admission_error"
    finally:
        if browser_lane and browser is not None:
            browser.finish_task(task)
        stopped.set()
        heartbeat.join()
        if acquired_probe and platform is not None and owner_token is not None:
            try:
                circuits.release_probe(
                    "platform", platform, owner_token=owner_token, now=now or current_iso(),
                )
            except Exception:  # noqa: BLE001 - Probe release cleanup must be logged without replacing the task result or failure
                get_logger("task_worker.loop").exception(
                    "task_worker_probe_release_failed task_id=%s", task.task_id,
                )
        if resource is not None and not processor_completed:
            RuntimeResourceLeaseRepository(base_path).release(
                resource.resource_kind, resource.resource_key,
                owner_token=resource.owner_token,
            )
