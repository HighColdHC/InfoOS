"""Preserve a successful primary FETCH while applying supplemental auth effects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.application.tasks import platform_circuits
from backend.application.tasks.auth_recheck import recheck_auth_failure
from backend.application.workflows.acquisition_failures import AcquisitionFailure, AcquisitionFailureError
from backend.domain.tasks import TaskType
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.domain.failures.taxonomy import FetchFailureReason


@dataclass
class SupplementalFetchFailure:
    error: AcquisitionFailureError
    effect: dict[str, Any]
    callback: Any

    def complete(self, base_path, task, *, platform, owner_token, now):
        reason = self.error.failure.reason
        if reason in platform_circuits.MANUAL_PLATFORM_FAILURES:
            platform_circuits.complete_manual_platform_failure(base_path, self.effect, occurred_at=now)
        else:
            platform_circuits.record_platform_failure(base_path, task, platform_scope=platform,
                owner_token=owner_token, reason=reason, occurred_at=now)


def prepare_supplemental_failure(task, artifacts, base_path, *, platform, worker_id, now, resource):
    data = artifacts.get("digest_recheck_failure")
    if not data or not platform:
        return None
    error = AcquisitionFailureError(AcquisitionFailure(
        reason=FetchFailureReason(data["reason"]), message=data["message"],
        retryable=data["retryable"], user_action=data["user_action"], diagnostic=data["diagnostic"]))
    if error.failure.reason is FetchFailureReason.PLATFORM_NOT_LOGGED_IN:
        error = recheck_auth_failure(error, task, base_path, platform=platform,
                                    worker_id=worker_id, now=now, resource=resource)
    artifacts["digest_recheck_failure"] = {**data, "reason": error.failure.reason.value,
                                           "diagnostic": error.failure.diagnostic}
    effect: dict[str, Any] = {}
    callback = platform_circuits.manual_platform_failure_callback(effect, task,
        platform_scope=platform, reason=error.failure.reason, occurred_at=now)
    return SupplementalFetchFailure(error, effect, callback)


def complete_fetch_circuit(supplemental, base_path, task, *, platform, owner_token, now):
    if supplemental:
        supplemental.complete(base_path, task, platform=platform, owner_token=owner_token, now=now)
    elif task.task_type is TaskType.FETCH and platform is not None:
        RuntimeCircuitRepository(base_path).record_success("platform", platform, occurred_at=now,
            owner_token=owner_token, task_id=task.task_id, chain_id=task.artifacts_summary.get("chain_id"))
