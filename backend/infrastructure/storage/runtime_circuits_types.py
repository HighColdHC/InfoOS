"""Rows owned by runtime_circuits queries."""
from typing import TypedDict


class CircuitRow(TypedDict):
    scope_kind: str
    scope_key: str
    state: str
    recovery_mode: str
    outage_epoch: int
    consecutive_failures: int
    opened_at: str | None
    cooldown_until: str | None
    last_failure_at: str | None
    last_success_at: str | None
    reason_code: str | None
    user_action: str | None
    probe_owner_token: str | None
    probe_acquired_at: str | None
    probe_lease_expires_at: str | None
    updated_at: str


class SafeCircuitRow(TypedDict):
    scope_kind: str
    scope_key: str
    state: str
    consecutive_failures: int
    outage_epoch: int
    opened_at: str | None
    next_probe_at: str | None
    reason_code: str | None
    last_failure_at: str | None
    last_success_at: str | None
    user_action: str
    updated_at: str
