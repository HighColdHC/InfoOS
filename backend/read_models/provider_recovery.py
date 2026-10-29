"""Safe provider-recovery projection shared by task and card read models."""
from __future__ import annotations


from pathlib import Path
from typing import TypedDict

from backend.domain.json_types import JsonObject
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.infrastructure.storage.runtime_circuits import RuntimeCircuitRepository


class ProviderRecovery(TypedDict):
    state: str
    trigger: object
    provider_id: str | None
    outage_epoch: int | None
    automatic_recoveries_used: int
    automatic_recovery_limit: int
    next_retry_at: object
    manual_retry_allowed: bool


class StoredRecovery(TypedDict):
    provider_id: str | None
    automatic_recoveries_used: int
    state: str
    trigger: object
    outage_epoch: int | None
    next_retry_at: object


def _parse_recovery(artifacts: JsonObject) -> StoredRecovery:
    value = artifacts.get("provider_recovery")
    stored: JsonObject = value if isinstance(value, dict) else {}
    return {
        "provider_id": str(stored.get("provider_id") or artifacts.get("provider_id") or "") or None,
        "automatic_recoveries_used": int(stored.get("automatic_recoveries_used") or 0),
        "state": str(stored.get("state") or "none"),
        "trigger": stored.get("trigger"),
        "outage_epoch": int(stored["outage_epoch"]) if stored.get("outage_epoch") is not None else None,
        "next_retry_at": stored.get("next_retry_at"),
    }


AUTOMATIC_RECOVERY_LIMIT = 1

def provider_recovery_diagnostic(
    base_dir: Path | str, task: Task | None
) -> ProviderRecovery:
    empty: ProviderRecovery = {
        "state": "none",
        "trigger": None,
        "provider_id": None,
        "outage_epoch": None,
        "automatic_recoveries_used": 0,
        "automatic_recovery_limit": AUTOMATIC_RECOVERY_LIMIT,
        "next_retry_at": None,
        "manual_retry_allowed": False,
    }
    if task is None or task.task_type != TaskType.CARD_LLM_ENRICH:
        return empty
    stored = _parse_recovery(task.artifacts_summary)
    provider_id = stored["provider_id"]
    circuit = (
        RuntimeCircuitRepository(base_dir).get("provider", provider_id)
        if provider_id
        else None
    )
    used = stored["automatic_recoveries_used"]
    state = stored["state"]
    if task.status == TaskStatus.RUNNING and stored["trigger"]:
        state = "running"
    elif task.status == TaskStatus.SUCCESS and stored["trigger"]:
        state = "recovered"
    elif task.status == TaskStatus.FAILED and task.error_reason == "provider_transient":
        if used >= AUTOMATIC_RECOVERY_LIMIT:
            state = "recovery_exhausted"
        elif circuit and circuit["state"] in {"open", "half_open"}:
            state = "waiting_circuit"
        elif state not in {"scheduled", "waiting_circuit"}:
            state = "none"
    elif task.status == TaskStatus.PENDING and stored["trigger"]:
        state = (
            "waiting_circuit"
            if stored["state"] == "waiting_circuit"
            else "scheduled"
        )
    if state == "recovery_exhausted":
        state = "exhausted"
    return {
        "state": state
        if state
        in {
            "none", "waiting_circuit", "scheduled", "running", "exhausted", "recovered",
        }
        else "none",
        "trigger": stored["trigger"]
        if stored["trigger"] in {"automatic", "manual"}
        else None,
        "provider_id": provider_id,
        "outage_epoch": (
            stored["outage_epoch"]
            if stored["outage_epoch"] is not None
            else circuit["outage_epoch"]
            if circuit and circuit["outage_epoch"] > 0
            else None
        ),
        "automatic_recoveries_used": used,
        "automatic_recovery_limit": AUTOMATIC_RECOVERY_LIMIT,
        "next_retry_at": task.next_run_at
        if task.status == TaskStatus.PENDING
        else stored["next_retry_at"],
        "manual_retry_allowed": False,
    }
