"""Half-open ZSXQ admission tests one list request before spending an attempt."""
from pathlib import Path

from backend.application.tasks.execution_clock import current_iso
from backend.application.tasks.platform_circuits import record_platform_failure
from backend.application.workflows.acquisition_failures import AcquisitionFailureError, classify_acquisition_failure
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.domain.sources.zsxq import zsxq_group_id
from backend.infrastructure.connectors.zsxq_runtime import run_probe
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository
from backend.infrastructure.storage.sources import SourceRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


class CircuitProbeDeferred(Exception):
    def __init__(self, retry_at: str):
        self.retry_at = retry_at


def probe_before_task(task, base_path: Path, *, owner: str, now: str | None, heartbeat) -> None:
    source = SourceRepository(base_path).get_source(task.target_id) or {}
    metadata = source.get("raw_metadata") or {}
    # ResearchConnector currently targets the default group; maintained list
    # sources carry their explicit group_id in raw_metadata.
    group_id = zsxq_group_id({} if source.get("source_type") == "research" else metadata)
    circuits = RuntimeCircuitRepository(base_path)
    try:
        run_probe(group_id)
    except Exception as error:
        failure = classify_acquisition_failure(source={"platform": "zsxq"}, result=None, error=error)
        if failure is None:
            raise
        if failure.reason is not FetchFailureReason.RATE_LIMITED:
            raise AcquisitionFailureError(failure) from error
        occurred_at = now or current_iso()
        heartbeat.finish(lambda: record_platform_failure(
            base_path, task, platform_scope="zsxq", owner_token=owner,
            reason=FetchFailureReason.RATE_LIMITED, occurred_at=occurred_at,
        ))
        row = circuits.get("platform", "zsxq")
        retry_at = str(row["cooldown_until"])
        TaskEventRepository(base_path).append(
            event_type="circuit_probe_failed", task_id=task.task_id,
            chain_id=task.artifacts_summary.get("chain_id"), occurred_at=occurred_at,
            payload={"reason": "知识星球轻量探针仍受风控，原任务未执行，等待固定冷却",
                     "next_run_at": retry_at, "attempt": task.attempt},
        )
        raise CircuitProbeDeferred(retry_at) from error
    heartbeat.finish(lambda: circuits.record_success(
        "platform", "zsxq", occurred_at=now or current_iso(), owner_token=owner,
        task_id=task.task_id, chain_id=task.artifacts_summary.get("chain_id"),
    ))
