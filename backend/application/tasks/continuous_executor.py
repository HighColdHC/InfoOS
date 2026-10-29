"""Completion-driven durable worker for every execution lane."""
from __future__ import annotations

import json
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.application.tasks.admission import ResourceLease, ResourceRequest
from backend.application.tasks.candidate_lane_cache import CandidateLaneCache
from backend.application.tasks.continuous_browser import BrowserLifetime
from backend.application.tasks.continuous_task_execution import _execute_claimed, complete_checkpoint
from backend.application.tasks.execution_clock import add_seconds, current_iso
from backend.application.tasks.executor import (
    browser_fetch_worker_count, lease_duration_seconds, new_worker_id,
    public_fetch_worker_count, resolve_worker_count, empty_run_result,
    task_type_values, queue_name_values,
)
from backend.application.tasks.lanes import ExecutionLane, reachable_lanes_for
from backend.application.tasks.processor_throttle import peek_fetch_throttle_decision
from backend.application.tasks.registry import TaskHandler
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.resource_governor import resource_plan_for
from backend.application.tasks.types import Task, TaskType
from backend.application.tasks.synchronous_batch import SynchronousBatch
from backend.infrastructure.observability.logging import get_logger
from backend.infrastructure.storage.runtime_resource_leases import RuntimeResourceLeaseRepository


_SUMMARY_SECONDS = 60.0
_RECOVERY_SECONDS = 60.0
_MAX_PAGES_PER_REFILL = 4


def _percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percentage))], 3)


def _claim_wait_seconds(task: Task) -> float | None:
    if not task.created_at or not task.started_at:
        return None
    try:
        created = datetime.fromisoformat(task.created_at.replace("Z", "+00:00"))
        started = datetime.fromisoformat(task.started_at.replace("Z", "+00:00"))
        return max(0.0, (started - created).total_seconds())
    except (TypeError, ValueError):
        return None


def run_continuous_tasks(
    base_dir: str | Path, *, queue_name: str, stop_event: threading.Event,
    poll_seconds: float = 1.0, batch_limit: int = 50,
    max_workers: int | None = None, worker_name: str | None = None,
    handlers: dict[TaskType, TaskHandler] | None = None,
    summary_seconds: float = _SUMMARY_SECONDS,
    browser_lifetime: BrowserLifetime | None = None,
) -> int:
    """Refill each lane when one of its running tasks completes."""
    return _run_tasks(
        base_dir, queue_names=[queue_name], stop_event=stop_event,
        poll_seconds=poll_seconds, batch_limit=batch_limit, max_workers=max_workers,
        worker_name=worker_name, handlers=handlers, summary_seconds=summary_seconds,
        browser_lifetime=browser_lifetime,
    )["processed"]


def drain_once(
    base_dir: str | Path, *, queue_names: Iterable[str] | None = None,
    limit: int = 20, task_ids: list[str] | None = None, now: str | None = None,
    max_workers: int | None = None, task_types: Iterable[TaskType | str] | None = None,
    worker_name: str | None = None, handlers: dict[TaskType, TaskHandler] | None = None,
) -> dict[str, Any]:
    """Run a finite snapshot through production admission and wait for completion."""
    from backend.infrastructure.observability.logging import configure_logging

    configure_logging(Path(base_dir))
    if limit <= 0:
        return empty_run_result(max_workers=max_workers, recovered=0 if task_ids is None else None)
    batch = SynchronousBatch(
        limit, now, task_ids, task_type_values(task_types), queue_name_values(queue_names),
    )
    return _run_tasks(
        base_dir, queue_names=batch.queue_names, stop_event=threading.Event(),
        poll_seconds=0.01, batch_limit=limit, max_workers=max_workers,
        worker_name=worker_name, handlers=handlers, batch=batch,
    )


def _run_tasks(
    base_dir: str | Path, *, queue_names: list[str] | None, stop_event: threading.Event,
    poll_seconds: float, batch_limit: int, max_workers: int | None,
    worker_name: str | None, handlers: dict[TaskType, TaskHandler] | None,
    summary_seconds: float = _SUMMARY_SECONDS,
    browser_lifetime: BrowserLifetime | None = None,
    batch: SynchronousBatch | None = None,
) -> dict[str, Any]:
    base_path = Path(base_dir)
    repo = TaskRepository.for_base_dir(base_path)
    candidate_lanes = CandidateLaneCache(base_path)
    logger = get_logger("task_worker.loop")
    queue_name = "+".join(queue_names) if queue_names is not None else "all"
    worker_id = new_worker_id(worker_name=worker_name, queue_names=queue_names)
    execution_now = batch.now if batch is not None else None
    lease_seconds = lease_duration_seconds()
    capacities = {
        ExecutionLane.DEFAULT: resolve_worker_count(max_workers),
        ExecutionLane.LOCAL_IO: 1,
        ExecutionLane.PUBLIC_ACQUISITION: public_fetch_worker_count(),
        ExecutionLane.BROWSER_ACQUISITION: browser_fetch_worker_count(),
    }
    reachable_lanes = reachable_lanes_for(queue_names)
    browser = browser_lifetime or BrowserLifetime(base_path)
    pools = {
        lane: ThreadPoolExecutor(max_workers=capacity)
        for lane, capacity in capacities.items()
    }
    running: dict[Future[tuple[dict[str, Any] | None, str | None]], tuple[Task, ExecutionLane]] = {}
    inflight_sites: dict[Future[tuple[dict[str, Any] | None, str | None]], str] = {}
    counts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    claim_waits: list[float] = []
    processed_total = 0
    window_started = last_recovery = time.monotonic()
    scan_after: tuple[int, str, str, str, int] | int | None = None
    stop_logged = False

    def summarize(*, final: bool = False) -> None:
        nonlocal window_started
        elapsed = time.monotonic() - window_started
        busy = Counter(lane.value for _task, lane in running.values())
        payload: dict[str, Any] = {
            "queue_name": queue_name, "window_seconds": round(elapsed, 3),
            **{key: counts[key] for key in (
                "processed", "succeeded", "failed", "skipped", "requeued",
                "recovered", "resource_pressure_deferred", "claims_failed",
                "circuit_deferred", "due_query_count",
            )},
            "resource_pressure_reasons": dict(reasons),
            "due_page_depth_max": counts["due_page_depth_max"],
            "slots_busy": dict(busy),
            "slots_capacity": {lane.value: n for lane, n in capacities.items()},
            "enqueue_to_claim_p50": _percentile(claim_waits, 0.5),
            "enqueue_to_claim_p95": _percentile(claim_waits, 0.95),
            "stop_requested": stop_event.is_set(), "final": final,
        }
        logger.info("task_worker_period_summary %s", json.dumps(payload, sort_keys=True))
        if batch is None:
            counts.clear()
            reasons.clear()
        claim_waits.clear()
        window_started = time.monotonic()

    def collect_done() -> bool:
        nonlocal processed_total
        completed = [future for future in running if future.done()]
        for future in completed:
            inflight_sites.pop(future, None)
            task, _lane = running.pop(future)
            try:
                result, outcome = future.result()
            except Exception:
                logger.exception("task_worker_future_failed task_id=%s", task.task_id)
                result, outcome = None, "admission_error"
            if result is not None:
                counts["processed"] += 1
                processed_total += 1
                if batch is not None:
                    batch.results.append(result)
                status = str(result.get("status") or "")
                key = {"success": "succeeded", "failed": "failed",
                       "skipped": "skipped", "pending": "requeued"}.get(status)
                if key:
                    counts[key] += 1
            elif outcome:
                counts[outcome] += 1
        return bool(completed)

    try:
        if batch is None or batch.task_ids is None:
            snapshot_at = execution_now or current_iso()
            counts["recovered"] += repo.recover_expired_leases(
                snapshot_at, event_occurred_at=snapshot_at, base_dir=base_path,
            )
        if batch is not None:
            batch.load(repo, execution_now or current_iso())
        logger.info("task_worker_continuous_started queue_name=%s worker_id=%s", queue_name, worker_id)
        while not stop_event.is_set() or running:
            completed = collect_done()
            browser.close_if_idle()
            elapsed_now = time.monotonic()
            if batch is None and elapsed_now - last_recovery >= _RECOVERY_SECONDS and not stop_event.is_set():
                try:
                    now = current_iso()
                    counts["recovered"] += repo.recover_expired_leases(
                        now, event_occurred_at=now, base_dir=base_path,
                    )
                except Exception:
                    logger.exception("task_worker_recovery_failed queue_name=%s", queue_name)
                last_recovery = elapsed_now
            if elapsed_now - window_started >= summary_seconds:
                summarize()
            if stop_event.is_set():
                if not stop_logged:
                    logger.info("task_worker_stop_requested queue_name=%s in_flight=%s", queue_name, len(running))
                    stop_logged = True
                if running:
                    wait(tuple(running), timeout=max(0.01, poll_seconds), return_when=FIRST_COMPLETED)
                continue

            if batch is not None and batch.submitted >= batch.limit:
                if running:
                    wait(tuple(running), return_when=FIRST_COMPLETED)
                    continue
                break

            busy = Counter(lane for _task, lane in running.values())
            if all(busy[lane] >= capacities[lane] for lane in reachable_lanes):
                wait(tuple(running), timeout=max(0.01, poll_seconds), return_when=FIRST_COMPLETED)
                continue

            made_progress = False
            attempted_sites: set[str] = set(inflight_sites.values())
            carry_after = scan_after
            after = None
            page_size = min(50, max(16, 2 * max(capacities[lane] for lane in reachable_lanes), batch_limit))
            for depth in range(1, _MAX_PAGES_PER_REFILL + 1):
                if stop_event.is_set():
                    break
                if depth == 2 and not made_progress and carry_after is not None:
                    after = carry_after
                try:
                    if batch is None:
                        candidates, next_cursor = repo.list_due_page(
                            now=current_iso(), queue_names=queue_names,
                            limit=page_size, after=after,
                        )
                    else:
                        candidates, next_cursor = batch.page(limit=page_size, after=after)
                    counts["due_query_count"] += 1
                    counts["due_page_depth_max"] = max(counts["due_page_depth_max"], depth)
                except Exception:
                    logger.exception("task_worker_due_query_failed queue_name=%s", queue_name)
                    break
                if not candidates:
                    scan_after = None
                    break
                for task in candidates:
                    if stop_event.is_set():
                        break
                    if batch is not None and not batch.eligible(task):
                        continue
                    claimed_task: Task | None = None
                    resource: ResourceLease | None = None
                    submitted = False
                    try:
                        lane, candidate_site = candidate_lanes.get(task)
                        if lane not in reachable_lanes:
                            logger.warning(
                                "task_worker_unexpected_lane task_id=%s queue_name=%s lane=%s",
                                task.task_id, queue_name, lane.value,
                            )
                            reachable_lanes.add(lane)
                        if batch is not None:
                            batch.active_lanes.add(lane)
                        if busy[lane] >= capacities[lane]:
                            continue
                        replay_selected = False
                        site: str | None = None
                        if lane is ExecutionLane.BROWSER_ACQUISITION:
                            if not peek_fetch_throttle_decision(task, base_path, now=execution_now).allowed:
                                if batch is not None:
                                    batch.considered.add(task.task_id)
                                continue
                            replay_selected = complete_checkpoint(base_path, task)
                            if not replay_selected:
                                site = candidate_site
                                if site is not None:
                                    if site in attempted_sites:
                                        continue
                                    attempted_sites.add(site)
                        plan = resource_plan_for(task)
                        if not plan.allowed:
                            counts["resource_pressure_deferred"] += 1
                            reasons[plan.defer_reason or "resource_pressure"] += 1
                            if batch is not None:
                                batch.considered.add(task.task_id)
                            continue
                        if batch is not None:
                            batch.considered.add(task.task_id)
                        claimed_at = current_iso()
                        request = (
                            ResourceRequest("browser_site", site)
                            if site is not None else plan.resource_request
                        )
                        if request is not None:
                            admitted = repo.claim_task_ids_with_resources(
                                [task.task_id], worker_id=worker_id, now=claimed_at,
                                lease_expires_at=add_seconds(claimed_at, lease_seconds),
                                resource_requests={task.task_id: request},
                                queue_names=queue_names, event_occurred_at=execution_now or claimed_at,
                                **({"task_types": batch.task_types} if batch is not None else {}),
                            )
                            if admitted:
                                claimed_task, resource = admitted[0].task, admitted[0].resource
                        else:
                            claims = repo.claim_task_ids(
                                [task.task_id], worker_id=worker_id, now=claimed_at,
                                lease_expires_at=add_seconds(claimed_at, lease_seconds),
                                queue_names=queue_names, event_occurred_at=execution_now or claimed_at,
                                **({"task_types": batch.task_types} if batch is not None else {}),
                            )
                            if claims:
                                claimed_task = claims[0]
                        if claimed_task is None:
                            counts["claims_failed"] += 1
                            continue
                        future = pools[lane].submit(
                            _execute_claimed, claimed_task, base_path=base_path,
                            worker_id=worker_id, lease_seconds=lease_seconds,
                            resource=resource, handlers=handlers, lane=lane,
                            browser=browser, replay_selected=replay_selected, stop_event=stop_event,
                            **({"now": execution_now} if batch is not None else {}),
                        )
                        running[future] = (claimed_task, lane)
                        if site is not None:
                            inflight_sites[future] = site
                        submitted = True
                        if batch is not None:
                            batch.submitted += 1
                        busy[lane] += 1
                        delay = _claim_wait_seconds(claimed_task)
                        if delay is not None and len(claim_waits) < 1000:
                            claim_waits.append(delay)
                        made_progress = True
                    except Exception:
                        if batch is not None:
                            batch.considered.add(task.task_id)
                        logger.exception("task_worker_refill_failed task_id=%s queue_name=%s", task.task_id, queue_name)
                        if claimed_task is not None and not submitted:
                            repo.defer(
                                claimed_task.task_id, worker_id=worker_id,
                                next_run_at=add_seconds(execution_now or current_iso(), 1),
                                error_reason="refill_failed",
                            )
                            if resource is not None:
                                RuntimeResourceLeaseRepository(base_path).release(
                                    resource.resource_kind, resource.resource_key,
                                    owner_token=resource.owner_token,
                                )
                scan_after = next_cursor
                after = next_cursor
                if next_cursor is None or len(candidates) < page_size:
                    scan_after = None
                    break
                if all(busy[lane] >= capacities[lane] for lane in reachable_lanes):
                    break
            if not made_progress and not completed:
                if running:
                    wait(tuple(running), timeout=max(0.01, poll_seconds), return_when=FIRST_COMPLETED)
                elif batch is not None:
                    # A failed site claim can leave same-site candidates
                    # unattempted in this refill. Try them in another round.
                    if scan_after is None and not any(batch.eligible(task) for task in batch.snapshot):
                        break
                else:
                    stop_event.wait(max(0.0, poll_seconds))
    finally:
        for pool in pools.values():
            pool.shutdown(wait=True)
        browser.close_if_idle(force=True)
        summarize(final=True)
        logger.info("task_worker_continuous_stopped queue_name=%s processed=%s", queue_name, processed_total)
    if batch is not None:
        return batch.summarize(max_workers=max_workers, capacities=capacities, counts=counts, reasons=reasons)
    return {"processed": processed_total}
