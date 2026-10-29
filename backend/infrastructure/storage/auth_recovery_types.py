"""Rows owned by authentication recovery queries."""
from typing import TypedDict


class ActionableAuthRecoveryRow(TypedDict):
    incident_id: str
    platform: str
    outage_epoch: int
    reason_code: str
    status: str
    created_at: str
    updated_at: str
    resolved_at: str | None
    verification_started_at: str | None
    verification_claim_token: str | None
    last_error_code: str | None
    notification_status: str
    notified_at: str | None
    notification_attempts: int
    notification_lease_until: str | None
    notification_claim_token: str | None
    recovered_task_count: int
    affected_task_count: int
