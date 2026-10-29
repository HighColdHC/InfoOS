from __future__ import annotations

from backend.infrastructure.storage.runtime_circuits import (
    CircuitPolicy,
    RuntimeCircuitRepository,
)


def test_only_one_half_open_probe_is_admitted(tmp_path) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=60)
    circuits.record_failure(
        "provider",
        "provider-safe",
        occurred_at="2026-07-23T00:00:00Z",
        policy=policy,
    )

    first = circuits.admit(
        "provider",
        "provider-safe",
        owner_token="probe-one",
        now="2026-07-23T00:01:00Z",
        policy=policy,
    )
    second = circuits.admit(
        "provider",
        "provider-safe",
        owner_token="probe-two",
        now="2026-07-23T00:01:00Z",
        policy=policy,
    )

    assert first.allowed is True
    assert first.probe is True
    assert second.allowed is False
    assert second.state == "half_open"


def test_retry_after_extends_circuit_cooldown_and_success_closes(tmp_path) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=60)
    circuits.record_failure(
        "platform",
        "x",
        occurred_at="2026-07-23T00:00:00Z",
        retry_after_seconds=300,
        policy=policy,
    )
    assert not circuits.admit(
        "platform",
        "x",
        owner_token="early",
        now="2026-07-23T00:04:59Z",
        policy=policy,
    ).allowed
    probe = circuits.admit(
        "platform",
        "x",
        owner_token="probe",
        now="2026-07-23T00:05:00Z",
        policy=policy,
    )
    assert probe.allowed and probe.probe
    circuits.record_success(
        "platform",
        "x",
        occurred_at="2026-07-23T00:05:01Z",
        owner_token="probe",
    )
    row = circuits.get("platform", "x")
    assert row is not None
    assert row["state"] == "closed"
    assert row["consecutive_failures"] == 0


def test_crashed_half_open_probe_is_reclaimed_after_probe_lease(tmp_path) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(
        failure_threshold=1,
        cooldown_seconds=1,
        probe_lease_seconds=10,
    )
    circuits.record_failure(
        "provider",
        "provider-safe",
        occurred_at="2026-07-23T00:00:00Z",
        policy=policy,
    )
    assert circuits.admit(
        "provider",
        "provider-safe",
        owner_token="crashed",
        now="2026-07-23T00:00:01Z",
        policy=policy,
    ).allowed
    assert not circuits.admit(
        "provider",
        "provider-safe",
        owner_token="early",
        now="2026-07-23T00:00:10Z",
        policy=policy,
    ).allowed
    reclaimed = circuits.admit(
        "provider",
        "provider-safe",
        owner_token="replacement",
        now="2026-07-23T00:00:11Z",
        policy=policy,
    )
    assert reclaimed.allowed and reclaimed.probe


def test_transient_failure_cannot_downgrade_manual_recovery(tmp_path) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    circuits.open_manual(
        "provider",
        "provider-safe",
        occurred_at="2026-07-23T00:00:00Z",
        reason_code="config_required",
        user_action="config_required",
    )

    state = circuits.record_failure(
        "provider",
        "provider-safe",
        occurred_at="2026-07-23T00:00:01Z",
        reason_code="provider_transient",
    )

    row = circuits.get("provider", "provider-safe")
    assert state == "open"
    assert row["recovery_mode"] == "manual"
    assert row["reason_code"] == "config_required"


def test_non_owner_cannot_close_or_emit_close_for_half_open_probe(tmp_path) -> None:
    from backend.infrastructure.storage.task_events import TaskEventRepository

    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=1)
    circuits.record_failure(
        "platform",
        "x",
        occurred_at="2026-07-23T00:00:00Z",
        policy=policy,
    )
    assert circuits.admit(
        "platform",
        "x",
        owner_token="probe-owner",
        now="2026-07-23T00:00:01Z",
        policy=policy,
    ).allowed

    circuits.record_success(
        "platform",
        "x",
        occurred_at="2026-07-23T00:00:02Z",
        owner_token="not-owner",
        task_id="not-owner-task",
    )

    assert circuits.get("platform", "x")["state"] == "half_open"
    assert not any(
        event["event_type"] == "circuit_closed"
        for event in TaskEventRepository(tmp_path).list_events(
            task_id="not-owner-task"
        )
    )
