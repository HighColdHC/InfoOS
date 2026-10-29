"""The retired LLM quota cannot degrade the current collection product."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.infrastructure.storage.database import connect_existing, default_database_path
from backend.infrastructure.storage.migrations import runner
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.read_models.collector_status import read_collector_status
from tests.infoos._factories import make_source_payload
from tests.infoos.storage.migration_test_helpers import drop_v23_indexes

NOW = "2026-10-28T00:00:00Z"


@pytest.mark.parametrize("interrupt", [False, True])
def test_retired_circuits_are_audited_and_removed_atomically(tmp_path, monkeypatch, interrupt):
    path = default_database_path(tmp_path)
    runner.migrate_database(path)
    circuits = RuntimeCircuitRepository(tmp_path)
    for kind, key in [("provider", "provider_default"), ("platform", "scys"), ("platform", "wechat")]:
        circuits.open_manual(kind, key, occurred_at=NOW, reason_code="quota_exceeded", user_action="restore_provider_quota")
    circuits.record_failure("platform", "x", occurred_at=NOW, policy=CircuitPolicy(failure_threshold=1))
    with connect_existing(path) as conn:
        conn.execute("insert into task_events(event_id,event_type,payload,occurred_at) values('old-event','circuit_opened','{}',?)", (NOW,))
        conn.execute("""insert into runtime_resource_leases(resource_kind,resource_key,
            owner_token,task_id,acquired_at,lease_expires_at,updated_at)
            values('browser','chrome','owner','fetch',?,'2026-10-29T00:00:00Z',?)""", (NOW, NOW))
        resource_before = tuple(conn.execute("select * from runtime_resource_leases").fetchone())
        drop_v23_indexes(conn)
        conn.execute("pragma user_version=21")
        conn.commit()
    if interrupt:
        def fail(event):
            if event["migration"] == 22:
                raise RuntimeError("interrupted")
        with pytest.raises(RuntimeError, match="interrupted"):
            runner.migrate_database(path, progress_callback=fail)
        with connect_existing(path) as conn:
            assert conn.execute("pragma user_version").fetchone()[0] == 21
            assert conn.execute("select count(*) from runtime_circuits").fetchone()[0] == 4
            assert conn.execute("select count(*) from task_events where event_type='runtime_circuit_retired'").fetchone()[0] == 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(r.after_version == runner.LATEST_VERSION for r in pool.map(runner.migrate_database, [path, path]))
    assert runner.migrate_database(path).batches == ()
    with connect_existing(path) as conn:
        assert [(r[0], r[1]) for r in conn.execute("select scope_kind,scope_key from runtime_circuits")] == [("platform", "x")]
        audits = [json.loads(r[0]) for r in conn.execute("select payload from task_events where event_type='runtime_circuit_retired'")]
        assert {a["scope_key"] for a in audits} == {"provider_default", "scys", "wechat"}
        assert len(audits) == 3
        assert conn.execute("select count(*) from task_events where event_id='old-event'").fetchone()[0] == 1
        assert tuple(conn.execute("select * from runtime_resource_leases").fetchone()) == resource_before
        assert conn.execute("pragma quick_check").fetchone()[0] == "ok"
    assert [r["scope_key"] for r in circuits.list_safe()] == ["x"]


def test_current_status_ignores_retired_scopes_before_cleanup(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(make_source_payload(source_id="feed", connector_id="feed", identifier="https://example.test/feed"))
    monkeypatch.setattr("backend.read_models.collector_status.read_container_health", lambda: {"status": "healthy"})
    circuits = RuntimeCircuitRepository(tmp_path)
    circuits.open_manual("provider", "provider_default", occurred_at=NOW, reason_code="quota_exceeded", user_action="restore_provider_quota")
    circuits.open_manual("platform", "wechat", occurred_at=NOW, reason_code="platform_not_logged_in", user_action="login_required")
    status = read_collector_status(tmp_path)
    assert status["service_status"] == "ready"
    assert status["providers"] == []
    assert status["reasons"] == []
    assert circuits.list_safe() == []
    circuits.open_manual("platform", "rss:feed", occurred_at=NOW, reason_code="config_required", user_action="config_required")
    assert [r["scope_key"] for r in circuits.list_safe()] == ["rss:feed"]
    assert circuits.admit("platform", "rss:feed", owner_token="probe", now=NOW).allowed is False
