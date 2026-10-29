from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.infoos.storage.migration_test_helpers import migrate_database_path
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository

NOW = "2026-07-23T00:00:00Z"


def test_outage_epoch_increments_only_closed_to_open_and_migrates_idempotently(
    tmp_path: Path,
) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=1, probe_lease_seconds=1)
    circuits.record_failure("provider", "provider_test", occurred_at=NOW, policy=policy)
    assert circuits.get("provider", "provider_test")["outage_epoch"] == 1
    admission = circuits.admit(
        "provider",
        "provider_test",
        owner_token="probe",
        now="2026-07-23T00:00:02Z",
        policy=policy,
    )
    assert admission.probe is True
    circuits.record_failure(
        "provider",
        "provider_test",
        occurred_at="2026-07-23T00:00:03Z",
        owner_token="probe",
        policy=policy,
    )
    assert circuits.get("provider", "provider_test")["outage_epoch"] == 1
    migrate_database_path(resolve_sqlite_path(tmp_path))
    migrate_database_path(resolve_sqlite_path(tmp_path))
    assert circuits.get("provider", "provider_test")["outage_epoch"] == 1


@pytest.mark.no_storage_bootstrap
def test_old_database_adds_outage_epoch_idempotently(tmp_path: Path) -> None:
    db_path = resolve_sqlite_path(tmp_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table runtime_circuits (
              scope_kind text not null, scope_key text not null, state text not null default 'closed',
              recovery_mode text not null default 'automatic', consecutive_failures integer not null default 0,
              opened_at text, cooldown_until text, last_failure_at text,
              last_success_at text, reason_code text, user_action text,
              probe_owner_token text, probe_acquired_at text,
              probe_lease_expires_at text, updated_at text not null,
              primary key(scope_kind, scope_key)
            )
            """
        )
    migrate_database_path(db_path)
    migrate_database_path(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute("pragma table_info(runtime_circuits)")
        }
    assert "outage_epoch" in columns
