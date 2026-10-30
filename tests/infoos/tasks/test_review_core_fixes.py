from __future__ import annotations

import sqlite3
from pathlib import Path
import pytest

from backend.application.tasks.executor import run_tasks_by_ids
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType
from backend.infrastructure.storage.runtime_circuits import CircuitPolicy, RuntimeCircuitRepository
from backend.infrastructure.storage.runtime_resource_leases import RuntimeResourceLeaseRepository
from backend.infrastructure.storage.repositories import CardRepository
from backend.infrastructure.storage.task_events import TaskEventRepository


NOW = "2026-09-22T00:02:00Z"


def _expired_task(task_id: str, *, max_attempts: int = 3) -> Task:
    return Task(
        task_id, TaskType.FETCH, "source", "source-1",
        status=TaskStatus.RUNNING, claimed_by="dead-worker",
        lease_expires_at="2026-09-22T00:01:00Z", max_attempts=max_attempts,
    )


def test_expired_lease_counts_execution_and_backs_off(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_expired_task("retry"))
    assert repo.recover_expired_leases(NOW, event_occurred_at=NOW) == 1
    task = repo.get("retry")
    assert task.status is TaskStatus.PENDING
    assert task.attempt == 2
    assert task.next_run_at == "2026-09-22T00:04:00Z"
    event = TaskEventRepository(tmp_path).list_events()[0]
    assert event["payload"]["attempt"] == 2
    assert event["payload"]["exhausted"] is False


def test_expired_lease_cap_finalizes_and_blocks_success_dependency(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_expired_task("exhausted", max_attempts=1))
    repo.enqueue(Task(
        "dependent", TaskType.CARD_BUILD, "card", "card-1",
        depends_on=(DependencySpec("exhausted", TaskStatus.SUCCESS),),
    ))
    assert repo.recover_expired_leases(NOW, event_occurred_at=NOW) == 1
    task = repo.get("exhausted")
    assert task.status is TaskStatus.FAILED
    assert task.attempt == 2
    assert task.error_reason == "lease_expired_attempts_exhausted"
    assert task.last_failure_class == "lease_expired"
    assert task.completion_token
    assert repo.get("dependent").status is TaskStatus.PENDING
    assert "dependent" not in {item.task_id for item in repo.list_due(now=NOW)}
    events = TaskEventRepository(tmp_path).list_events()
    assert [(event["event_type"], event["payload"].get("exhausted")) for event in events] == [
        ("failed", True), ("lease_recovered", True),
    ]


def test_exhausted_content_enrich_uses_existing_finalize_transition(tmp_path: Path) -> None:
    card_id = "lease-card"
    CardRepository(tmp_path).upsert_card({
        "card_id": card_id, "source_id": None, "source_platform": "x",
        "source_type": "social_post", "source_url": "https://x.com/example/status/1",
        "source_author": "Example", "title": "Lease recovery", "content": "Durable body",
        "captured_at": NOW, "processed_at": NOW, "raw_payload": {},
    })
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        "content-lease", TaskType.CONTENT_ENRICH, "card", card_id,
        status=TaskStatus.RUNNING, claimed_by="dead-worker",
        lease_expires_at="2026-09-22T00:01:00Z", max_attempts=1,
    ))
    assert repo.recover_expired_leases(NOW) == 1
    finalizers = repo.list_tasks(task_type=TaskType.CARD_FINALIZE)
    assert len(finalizers) == 1
    assert finalizers[0].artifacts_summary["upstream_task_id"] == "content-lease"
    assert finalizers[0].artifacts_summary["media_gaps"][0]["status"] == "failed"
    assert finalizers[0].task_id in repo.get("content-lease").artifacts_summary["downstream_task_ids"]


def test_third_lost_execution_exhausts_three_attempt_cap(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_expired_task("three-losses", max_attempts=3))
    for recovered_at, claim_at, lease_end, expected_attempt in (
        ("2026-09-22T00:02:00Z", "2026-09-22T00:04:00Z", "2026-09-22T00:05:00Z", 2),
        ("2026-09-22T00:06:00Z", "2026-09-22T00:10:00Z", "2026-09-22T00:11:00Z", 3),
    ):
        assert repo.recover_expired_leases(recovered_at) == 1
        task = repo.get("three-losses")
        assert task.status is TaskStatus.PENDING
        assert task.attempt == expected_attempt
        claimed = repo.claim_task_ids(
            ["three-losses"], worker_id="dead-worker", now=claim_at,
            lease_expires_at=lease_end,
        )
        assert len(claimed) == 1
    assert repo.recover_expired_leases("2026-09-22T00:12:00Z") == 1
    task = repo.get("three-losses")
    assert task.status is TaskStatus.FAILED
    assert task.attempt == 4


def test_exhausted_recovery_requires_explicit_base_dir(tmp_path: Path) -> None:
    initialized = TaskRepository.for_base_dir(tmp_path)
    initialized.enqueue(_expired_task("no-base", max_attempts=1))
    bare_repo = TaskRepository(initialized.db_path)
    with pytest.raises(ValueError, match="base_dir is required"):
        bare_repo.recover_expired_leases(NOW)
    assert bare_repo.get("no-base").status is TaskStatus.RUNNING
    assert bare_repo.recover_expired_leases(NOW, base_dir=tmp_path) == 1
    assert bare_repo.get("no-base").status is TaskStatus.FAILED


def test_explicit_recovery_base_dir_overrides_repository_default(tmp_path: Path) -> None:
    card_id = "override-card"
    CardRepository(tmp_path).upsert_card({
        "card_id": card_id, "source_id": None, "source_platform": "x",
        "source_type": "social_post", "source_url": "https://x.com/example/status/2",
        "source_author": "Example", "title": "Explicit context", "content": "Durable body",
        "captured_at": NOW, "processed_at": NOW, "raw_payload": {},
    })
    seeded = TaskRepository.for_base_dir(tmp_path)
    seeded.enqueue(Task(
        "override-task", TaskType.CONTENT_ENRICH, "card", card_id,
        status=TaskStatus.RUNNING, claimed_by="dead-worker",
        lease_expires_at="2026-09-22T00:01:00Z", max_attempts=1,
    ))
    repo = TaskRepository(seeded.db_path, base_dir=tmp_path / "wrong")
    assert repo.recover_expired_leases(NOW, base_dir=tmp_path) == 1
    assert len(seeded.list_tasks(task_type=TaskType.CARD_FINALIZE)) == 1


def test_no_expired_lease_skips_write_connection(tmp_path: Path, monkeypatch) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("pending", TaskType.FETCH, "source", "source-1"))
    monkeypatch.setattr(repo, "_connect_for_claim", lambda: (_ for _ in ()).throw(AssertionError("write connection")))
    assert repo.recover_expired_leases(NOW) == 0


def test_closed_circuit_admission_skips_write_transaction(tmp_path: Path, monkeypatch) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    circuits.record_failure("platform", "x", occurred_at=NOW, policy=CircuitPolicy(failure_threshold=3))
    real_connection = circuits._connection
    statements: list[str] = []

    def read_only_connection():
        conn = real_connection()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(circuits, "_connection", read_only_connection)
    assert circuits.admit("platform", "x", owner_token="owner", now=NOW).state == "closed"
    assert not any(sql.lower().startswith("begin immediate") for sql in statements)


def test_probe_release_requires_current_owner(tmp_path: Path) -> None:
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=1)
    circuits.record_failure("platform", "x", occurred_at="2026-09-22T00:00:00Z", policy=policy)
    assert circuits.admit("platform", "x", owner_token="owner", now="2026-09-22T00:00:01Z", policy=policy).probe
    assert not circuits.release_probe("platform", "x", owner_token="other", now=NOW)
    assert circuits.release_probe("platform", "x", owner_token="owner", now=NOW)
    assert not circuits.release_probe("platform", "x", owner_token="owner", now=NOW)
    assert circuits.get("platform", "x")["probe_owner_token"] is None


def test_executor_releases_unclaimed_probe(tmp_path: Path, monkeypatch) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task("unclaimed", TaskType.CONTENT_ENRICH, "card", "card-1"))
    circuits = RuntimeCircuitRepository(tmp_path)
    policy = CircuitPolicy(failure_threshold=1, cooldown_seconds=1)
    circuits.record_failure("platform", "x", occurred_at="2026-09-22T00:00:00Z", policy=policy)
    monkeypatch.setattr("backend.application.tasks.continuous_task_execution.platform_scope_for_task", lambda *_: "x")
    monkeypatch.setattr(TaskRepository, "claim_task_ids", lambda *args, **kwargs: [])
    result = run_tasks_by_ids(tmp_path, ["unclaimed"], now=NOW)
    assert result["processed"] == 0
    assert circuits.get("platform", "x")["probe_owner_token"] is None


def test_due_limit_and_connections_close(tmp_path: Path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    for index in range(3):
        repo.enqueue(Task(f"due-{index}", TaskType.CONTENT_ENRICH, "card", f"card-{index}"))
    queue = repo.get("due-0").queue_name
    assert len(repo.list_due(now=NOW, queue_names=[queue], limit=2)) == 2
    assert len(repo.list_due(now=NOW, queue_names=[queue], limit=None)) == 3
    assert len(repo.list_due(now=NOW, limit=2)) == 2
    with repo._connect() as task_conn:
        assert task_conn.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert task_conn.execute("pragma busy_timeout").fetchone()[0] >= 10000
        assert task_conn.execute("pragma synchronous").fetchone()[0] == 1
    try:
        task_conn.execute("select 1")
        assert False, "task connection remains open"
    except sqlite3.ProgrammingError:
        pass
    leases = RuntimeResourceLeaseRepository(tmp_path)
    with leases._connect() as lease_conn:
        assert lease_conn.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert lease_conn.execute("pragma busy_timeout").fetchone()[0] >= 10000
        assert lease_conn.execute("pragma synchronous").fetchone()[0] == 1
    try:
        lease_conn.execute("select 1")
        assert False, "lease connection remains open"
    except sqlite3.ProgrammingError:
        pass
