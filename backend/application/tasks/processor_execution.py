"""Single-task execution and owner-safe persistence."""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from backend.application.tasks.admission import ResourceLease
from backend.application.tasks.recovery import defer_resource_contention
from backend.application.tasks.registry import TaskHandler, handler_for
from backend.application.tasks.execution_clock import add_seconds, current_iso, heartbeat_interval_seconds
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks import platform_circuits
from backend.application.tasks.supplemental_fetch_failure import prepare_supplemental_failure, complete_fetch_circuit
from backend.application.tasks.retry import classify_task_failure, decide_task_retry
from backend.application.tasks.transitions import enqueue_downstream, merged_task_artifacts
from backend.application.tasks.processor_throttle import fetch_throttle_decision
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.probe_heartbeat import ProbeHeartbeat
from backend.domain.retry_eligibility import build_retry_event_payload
from backend.domain.tasks import fetch_circuit_platform
from backend.domain.failures import OpenCLISiteLeaseTimeout, ResourceLeaseLost
from backend.infrastructure.observability.logging import get_logger
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.infrastructure.storage.runtime_resource_leases import RuntimeResourceLeaseRepository
from backend.domain.failures.taxonomy import FetchFailureReason


def process_task(
    task: Task,
    base_path: Path,
    *,
    now: str | None = None,
    handlers: dict[TaskType, TaskHandler] | None = None,
    worker_id: str,
    lease_seconds: int,
    resource_lease: ResourceLease | None = None,
    platform_scope: str | None = None,
    platform_circuit_owner: str | None = None,
    platform_circuit_probe: bool = False,
    probe_heartbeat: ProbeHeartbeat | None = None,
    handler_runner: Callable[[Task, Path, TaskHandler], dict[str, Any]] | None = None,
    external_heartbeat: tuple[threading.Event, threading.Event, threading.Thread] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    logger = get_logger("tasks.worker")
    repo = TaskRepository.for_base_dir(base_path)
    if platform_scope in {"zsxq", "x"}:
        circuit_now = now or current_iso()
        retry_at = platform_circuits.platform_cooldown_retry_at(
            base_path, platform_scope=platform_scope, owner_token=platform_circuit_owner, now=circuit_now,
        )
        if retry_at:
            returned = repo.defer_owned_for_circuit(
                task, worker_id=worker_id, next_run_at=retry_at, occurred_at=circuit_now,
                scope_kind="platform", scope_key=platform_scope, resource=resource_lease,
            )
            return task_result(task, TaskStatus.PENDING, artifacts=task.artifacts_summary) \
                if returned else lost_lease_result(task, base_path)
    throttle = fetch_throttle_decision(task, base_path, now=now)
    if not throttle.allowed:
        artifacts = {
            **task.artifacts_summary,
            "rate_limited": True,
            "throttle_key": throttle.throttle_key,
            "deferred_until": throttle.next_run_at,
            "rate_limit_reason": throttle.reason,
        }
        if not repo.defer(
            task.task_id,
            worker_id=worker_id,
            next_run_at=str(throttle.next_run_at),
            error_reason=f"fetch_rate_limited:{throttle.reason}",
            artifacts_summary=artifacts,
        ):
            _release_resource(base_path, resource_lease)
            return lost_lease_result(task, base_path)
        logger.info(
            "task_deferred_by_rate_limit task_id=%s target_id=%s throttle_key=%s next_run_at=%s reason=%s",
            task.task_id,
            task.target_id,
            throttle.throttle_key,
            throttle.next_run_at,
            throttle.reason,
        )
        result = task_result(
            task,
            TaskStatus.PENDING,
            artifacts=artifacts,
            error=f"fetch_rate_limited:{throttle.reason}",
        )
        _release_resource(base_path, resource_lease)
        return result

    if throttle.pacing is not None:
        task = replace(task, artifacts_summary={**task.artifacts_summary, "fetch_pacing": throttle.pacing})

    logger.info(
        "task_started task_id=%s task_type=%s target_kind=%s target_id=%s attempt=%s",
        task.task_id,
        task.task_type.value,
        task.target_kind,
        task.target_id,
        task.attempt,
    )
    from backend.application.tasks.zsxq_probe import CircuitProbeDeferred, probe_before_task
    if platform_circuit_probe and platform_scope == "zsxq":
        probe_heartbeat = probe_heartbeat or ProbeHeartbeat(base_path)
        probe_heartbeat.bind("platform", "zsxq", platform_circuit_owner)
    owns_heartbeat = external_heartbeat is None
    if external_heartbeat is None:
        stopped, ownership_lost = threading.Event(), threading.Event()
        heartbeat = threading.Thread(
            target=heartbeat_task,
            args=(repo, task.task_id, worker_id, stopped, lease_seconds,
                  resource_lease, base_path, ownership_lost, probe_heartbeat), daemon=True,
        )
        heartbeat.start()
    else:
        stopped, ownership_lost, heartbeat = external_heartbeat
    try:
        handler = (handlers or {}).get(task.task_type) or handler_for(task.task_type, now=now)
        def execute_handler(current_task, current_base):
            if platform_scope == "zsxq" and platform_circuit_probe:
                probe_before_task(current_task, current_base, owner=platform_circuit_owner,
                                  now=now, heartbeat=probe_heartbeat)
            return handler(current_task, current_base)

        result = (handler_runner(task, base_path, execute_handler) if handler_runner
                  else execute_handler(task, base_path))
        if ownership_lost.is_set():
            if resource_lease is not None:
                raise ResourceLeaseLost(resource_lease.resource_kind, resource_lease.resource_key)
            return lost_lease_result(task, base_path)
        status = TaskStatus(str(result.get("status") or TaskStatus.SUCCESS.value))
        artifacts = merged_task_artifacts(task, dict(result.get("artifacts") or {}), status=status)
        if status == TaskStatus.PENDING:
            deferred_at = str(
                result.get("next_run_at") or add_seconds(now or current_iso(), 1)
            )
            error = str(result.get("error") or "handler_deferred")
            if not repo.defer(
                task.task_id,
                worker_id=worker_id,
                next_run_at=deferred_at,
                error_reason=error,
                artifacts_summary=artifacts,
            ):
                return lost_lease_result(task, base_path)
            return task_result(task, TaskStatus.PENDING, artifacts=artifacts, error=error)
        if status == TaskStatus.SKIPPED:
            durable = repo.get(task.task_id)
            if (
                durable is not None
                and durable.status == TaskStatus.SKIPPED
                and durable.artifacts_summary.get("reason") == "superseded"
            ):
                return task_result(
                    task,
                    TaskStatus.SKIPPED,
                    artifacts=durable.artifacts_summary,
                    error="superseded",
                )
        if status == TaskStatus.FAILED:
            return persist_task_failure(
                repo,
                task,
                worker_id=worker_id,
                now=now or current_iso(),
                error=result.get("error") or "handler reported failed status",
                artifacts=artifacts,
                base_path=base_path,
                platform_scope=platform_scope,
                platform_circuit_owner=platform_circuit_owner,
                resource_lease=resource_lease,
                stop_event=stop_event,
            )
        supplemental = prepare_supplemental_failure(task, artifacts, base_path, platform=platform_scope,
            worker_id=worker_id, now=now or current_iso(), resource=resource_lease)
        finalized = repo.finalize_terminal_with_downstream(
            task.task_id,
            worker_id=worker_id,
            status=status,
            artifacts=artifacts,
            error_reason=result.get("error"),
            result_version=result.get("result_version"),
            finished_at=now or current_iso(),
            enqueue_downstream=lambda transaction_repo: enqueue_downstream(
                transaction_repo,
                task,
                status=status,
                artifacts=artifacts,
                base_dir=base_path,
            ),
            event_chain_id=task.artifacts_summary.get("chain_id"),
            event_payload={"attempt": task.attempt},
            transaction_callback=supplemental.callback if supplemental else None,
            required_resource_owner=(
                (
                    resource_lease.resource_kind,
                    resource_lease.resource_key,
                    resource_lease.owner_token,
                )
                if resource_lease is not None
                else None
            ),
        )
        if finalized is None:
            return lost_lease_result(task, base_path)
        durable_artifacts, downstream_ids = finalized
        _complete_setup_first_fetch_if_requested(
            task,
            status=status,
            artifacts=durable_artifacts,
        )
        complete_fetch_circuit(supplemental, base_path, task, platform=platform_scope,
                               owner_token=platform_circuit_owner, now=now or current_iso())
        logger.info(
            "task_completed task_id=%s task_type=%s status=%s downstream_count=%s",
            task.task_id,
            task.task_type.value,
            status.value,
            len(downstream_ids),
        )
        return task_result(task, status, artifacts=durable_artifacts, downstream_ids=downstream_ids)
    except CircuitProbeDeferred as exc:
        returned = repo.defer_owned_for_circuit(
            task, worker_id=worker_id, next_run_at=exc.retry_at, occurred_at=now or current_iso(),
            scope_kind="platform", scope_key="zsxq", resource=resource_lease,
        )
        return task_result(task, TaskStatus.PENDING, artifacts=task.artifacts_summary) \
            if returned else lost_lease_result(task, base_path)
    except (ResourceLeaseLost, OpenCLISiteLeaseTimeout) as exc:
        resource_kind = (
            exc.resource_kind
            if isinstance(exc, ResourceLeaseLost)
            else "browser_site"
        )
        resource_key = (
            exc.resource_key
            if isinstance(exc, ResourceLeaseLost)
            else exc.site
        )
        decision = defer_resource_contention(
            repo,
            task,
            worker_id=worker_id,
            now=now or current_iso(),
            resource_kind=resource_kind,
            resource_key=resource_key,
        )
        if decision is None:
            return lost_lease_result(task, base_path)
        return task_result(task, TaskStatus.PENDING, artifacts=task.artifacts_summary, error=decision.reason)
    except Exception as exc:  # noqa: BLE001 - Worker task boundary must persist failures from every handler including subprocess and library errors
        artifacts: dict[str, Any] = {}
        if throttle.pacing is not None:
            # Re-read so the handler's checkpoint writes are not overwritten.
            durable_task = repo.get(task.task_id)
            if durable_task is None:
                return lost_lease_result(task, base_path)
            artifacts = {**durable_task.artifacts_summary, "fetch_pacing": throttle.pacing}
        return persist_task_failure(
            repo,
            task,
            worker_id=worker_id,
            now=now or current_iso(),
            error=exc,
            artifacts=artifacts,
            base_path=base_path,
            platform_scope=platform_scope,
            platform_circuit_owner=platform_circuit_owner,
            resource_lease=resource_lease,
            stop_event=stop_event,
        )
    finally:
        if owns_heartbeat:
            stopped.set()
            heartbeat.join()
        _release_resource(base_path, resource_lease)


def _release_resource(base_path: Path, resource_lease: ResourceLease | None) -> None:
    if resource_lease is None:
        return
    RuntimeResourceLeaseRepository(base_path).release(
        resource_lease.resource_kind,
        resource_lease.resource_key,
        owner_token=resource_lease.owner_token,
    )


def heartbeat_task(
    repo: TaskRepository,
    task_id: str,
    worker_id: str,
    stopped: threading.Event,
    lease_seconds: int,
    resource_lease: ResourceLease | None = None,
    base_path: Path | None = None,
    ownership_lost: threading.Event | None = None,
    probe_heartbeat: ProbeHeartbeat | None = None,
) -> None:
    interval = heartbeat_interval_seconds(lease_seconds)
    while not stopped.is_set():
        now = current_iso()
        lease_expires_at = add_seconds(now, lease_seconds)
        try:
            if resource_lease is not None and base_path is not None:
                renewed = repo.heartbeat_with_resource(
                    task_id, worker_id=worker_id, resource=resource_lease,
                    now=now, lease_expires_at=lease_expires_at,
                )
            else:
                renewed = repo.heartbeat(
                    task_id, worker_id=worker_id, now=now,
                    lease_expires_at=lease_expires_at,
                )
        except Exception:  # noqa: BLE001 - Heartbeat thread must log renewal failures and signal lost ownership to the task processor
            get_logger("tasks.worker").exception("task_heartbeat_failed task_id=%s", task_id)
            if ownership_lost is not None:
                ownership_lost.set()
            return
        if not renewed:
            if ownership_lost is not None:
                ownership_lost.set()
            return
        if probe_heartbeat is not None:
            try:
                probe_owned = probe_heartbeat.renew(now)
            except Exception:  # noqa: BLE001 - Heartbeat thread must log probe renewal failures without exiting silently
                get_logger("tasks.worker").exception(
                    "probe_heartbeat_failed task_id=%s", task_id,
                )
            else:
                if not probe_owned:
                    if ownership_lost is not None:
                        ownership_lost.set()
                    return
        probe_interval = probe_heartbeat.interval_seconds() if probe_heartbeat else None
        if stopped.wait(min(interval, probe_interval) if probe_interval else interval):
            return


def persist_task_failure(
    repo: TaskRepository,
    task: Task,
    *,
    worker_id: str,
    now: str,
    error: BaseException | str,
    artifacts: dict[str, Any],
    base_path: Path,
    platform_scope: str | None = None,
    platform_circuit_owner: str | None = None,
    resource_lease: ResourceLease | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    logger = get_logger("tasks.worker")
    reason = classify_task_failure(task, error)
    platform_failure = task.task_type is TaskType.FETCH and platform_scope is not None \
        and platform_circuits.is_platform_upstream_failure(error)
    if platform_failure and reason is FetchFailureReason.PLATFORM_NOT_LOGGED_IN:
        from backend.application.tasks.auth_recheck import recheck_auth_failure
        error = recheck_auth_failure(error, task, base_path, platform=platform_scope,
                                    worker_id=worker_id, now=now, resource=resource_lease,
                                    stop_event=stop_event)
        reason = error.failure.reason
    manual_platform_failure = platform_failure \
        and reason in platform_circuits.MANUAL_PLATFORM_FAILURES
    structured_artifacts = _structured_failure_artifacts(error, reason.value)
    if structured_artifacts:
        artifacts = {**task.artifacts_summary, **artifacts, **structured_artifacts}
    decision = decide_task_retry(task, reason=reason, now=now, platform=fetch_circuit_platform(platform_scope))
    error_reason = _safe_error_reason(error, reason)
    if platform_scope == "zsxq" and reason == FetchFailureReason.RATE_LIMITED:
        error_reason = "知识星球风控限流，等待自然冷却后重试"
        if decision.status == TaskStatus.FAILED:
            error_reason = "知识星球风控限流，自动重试已耗尽；可在冷却后重新发起"
    if platform_scope == "x" and reason == FetchFailureReason.RATE_LIMITED:
        error_reason = "X 搜索限流（429），等待冷却后重试"
        if decision.status == TaskStatus.FAILED:
            error_reason = "X 搜索限流（429），自动重试已耗尽；可在冷却后重新发起"
    retry_event_payload = build_retry_event_payload(
        error,
        attempt=decision.attempt,
        next_run_at=decision.next_run_at,
        failure_class=decision.failure_class,
    )
    stderr_summary = structured_artifacts.get("failure_diagnostic", {}).get("stderr_summary")
    if stderr_summary:
        retry_event_payload["diagnostic"]["stderr_summary"] = stderr_summary
    if recheck := structured_artifacts.get("failure_diagnostic", {}).get("auth_recheck"):
        retry_event_payload["auth_recheck"] = recheck
    manual_effect: dict[str, Any] = {}
    if decision.status == TaskStatus.PENDING:
        if not repo.fail_or_retry(
            task.task_id,
            worker_id=worker_id,
            decision=decision,
            error_reason=error_reason,
            updated_at=now,
            artifacts=artifacts or None,
            event_chain_id=task.artifacts_summary.get("chain_id"),
            event_payload=retry_event_payload,
            event_occurred_at=now,
        ):
            return lost_lease_result(task, base_path)
        downstream_ids: list[str] = []
    else:
        finalized = repo.finalize_terminal_with_downstream(
            task.task_id,
            worker_id=worker_id,
            status=decision.status,
            artifacts=artifacts,
            error_reason=error_reason,
            result_version=None,
            finished_at=now,
            attempt=decision.attempt,
            failure_class=decision.failure_class,
            clear_next_run_at=True,
            preserve_result_metadata=True,
            enqueue_downstream=lambda transaction_repo: enqueue_downstream(
                transaction_repo,
                task,
                status=decision.status,
                artifacts=artifacts,
                base_dir=base_path,
            ),
            event_chain_id=task.artifacts_summary.get("chain_id"),
            event_payload=retry_event_payload,
            event_failure_class=decision.failure_class,
            transaction_callback=platform_circuits.manual_platform_failure_callback(
                manual_effect, task, platform_scope=platform_scope,
                reason=reason, occurred_at=now,
            ) if manual_platform_failure else None,
        )
        if finalized is None:
            return lost_lease_result(task, base_path)
        artifacts, downstream_ids = finalized
    platform_circuits.release_source_unavailable_probe(
        base_path, platform_scope=platform_scope, owner_token=platform_circuit_owner,
        reason=reason, occurred_at=now,
    )
    if manual_platform_failure:
        platform_circuits.complete_manual_platform_failure(base_path, manual_effect, occurred_at=now)
    elif platform_failure:
        platform_circuits.record_platform_failure(
            base_path, task,
            platform_scope=str(platform_scope),
            owner_token=platform_circuit_owner,
            reason=reason, occurred_at=now,
        )
    logger.error(
        "task_failed task_id=%s task_type=%s status=%s downstream_count=%s",
        task.task_id,
        task.task_type.value,
        decision.status.value,
        len(downstream_ids),
    )
    return task_result(
        task,
        decision.status,
        artifacts=artifacts,
        error=error_reason,
        downstream_ids=downstream_ids,
    )


def _safe_error_reason(
    error: BaseException | str,
    reason: FetchFailureReason,
) -> str:
    from backend.application.workflows.acquisition_failures import AcquisitionFailureError

    if isinstance(error, AcquisitionFailureError):
        return reason.value
    return f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)


def _structured_failure_artifacts(error: BaseException | str, reason: str) -> dict[str, Any]:
    from backend.application.workflows.acquisition_failures import AcquisitionFailureError

    if not isinstance(error, AcquisitionFailureError):
        return {}
    diagnostic = dict(error.failure.diagnostic)
    return {
        "failure_reason": reason,
        "failure_diagnostic": diagnostic,
        "connector_diagnostic": diagnostic,
        "user_action": error.failure.user_action,
    }


def task_result(
    task: Task,
    status: TaskStatus,
    *,
    artifacts: dict[str, Any],
    error: str | None = None,
    downstream_ids: list[str] | None = None,
) -> dict[str, Any]:
    result = {
        "task_id": task.task_id,
        "task_type": task.task_type.value,
        "status": status.value,
        "artifacts": {**artifacts, "downstream_task_ids": downstream_ids or []},
        "downstream_task_ids": downstream_ids or [],
    }
    if error is not None:
        result["error"] = error
    return result


def lost_lease_result(task: Task, base_path: Path) -> dict[str, Any]:
    _record_task_event(
        base_path,
        event_type="lease_lost",
        task=task,
        status=TaskStatus.RUNNING,
        payload={"attempt": task.attempt},
    )
    return {
        "task_id": task.task_id,
        "task_type": task.task_type.value,
        "status": TaskStatus.RUNNING.value,
        "error": "lease_ownership_lost",
        "downstream_task_ids": [],
    }


def _complete_setup_first_fetch_if_requested(
    task: Task,
    *,
    status: TaskStatus,
    artifacts: dict[str, Any],
) -> None:
    """Let the worker, never the request route, close a setup first fetch."""
    if task.task_type != TaskType.FETCH or status != TaskStatus.SUCCESS:
        return
    if not bool(task.artifacts_summary.get("setup_first_fetch")):
        return
    from backend.application.setup.close import mark_setup_complete
    from backend.infrastructure.runtime.environment import setup_complete_flag_path, setup_state_path

    mark_setup_complete(
        setup_state_path(),
        setup_complete_flag_path(),
        fetched_count=int(artifacts.get("fetched_count") or 0),
    )


def _record_task_event(
    base_path: Path,
    *,
    event_type: str,
    task: Task,
    status: TaskStatus,
    payload: dict[str, Any],
    occurred_at: str | None = None,
    failure_class: str | None = None,
) -> None:
    TaskEventRepository(base_path).append(
        event_type=event_type,
        task_id=task.task_id,
        chain_id=task.artifacts_summary.get("chain_id"),
        status=status.value,
        failure_class=failure_class,
        payload=payload,
        occurred_at=occurred_at,
    )
