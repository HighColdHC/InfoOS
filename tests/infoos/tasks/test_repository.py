"""TaskRepository: tasks 表 CRUD（spec §1）。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import DependencySpec, Task, TaskType, TaskStatus
from backend.infrastructure.storage.task_events import TaskEventRepository


pytestmark = pytest.mark.no_storage_bootstrap


@pytest.fixture
def repo(tmp_path: Path):
    db = tmp_path / "test.db"
    # 简化：手工建 tasks 表
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            create table tasks (
                task_id text primary key,
                task_type text not null,
                target_kind text not null,
                target_id text not null,
                status text not null,
                depends_on text not null default '[]',
                attempt integer not null default 1,
                max_attempts integer not null default 3,
                next_run_at text,
                error_reason text,
                artifacts_summary text not null default '{}',
                claimed_by text,
                lease_expires_at text,
                heartbeat_at text,
                started_at text,
                finished_at text,
                input_version text not null default 'v1',
                result_version text,
                last_failure_class text,
                completion_token text,
                queue_name text,
                priority integer,
                deadline_at text,
                created_at text not null,
                updated_at text not null
            );
            create table sources (
                source_id text primary key,
                platform text
            );
            create table cards (
                card_id text primary key,
                source_platform text
            );
            create table card_assets (
                asset_id text primary key,
                card_id text not null
            );
            create index idx_card_assets_card on card_assets(card_id);
            create index idx_tasks_target on tasks(target_kind, target_id);
        """)
    return TaskRepository(str(db))


def test_task_ids_for_card_uses_card_and_asset_targets(repo):
    for task_id, target_kind, target_id in (
        ("card-task", "card", "card-1"),
        ("asset-task-a", "asset", "asset-a"),
        ("asset-task-b", "asset", "asset-b"),
        ("other-asset-task", "asset", "asset-other"),
    ):
        repo.enqueue(Task(task_id, TaskType.CARD_FINALIZE, target_kind, target_id))
    with repo._connect() as conn:
        conn.executemany(
            "insert into card_assets(asset_id, card_id) values (?, ?)",
            (("asset-a", "card-1"), ("asset-b", "card-1"), ("asset-other", "card-2")),
        )

    assert repo.task_ids_for_card("card-1") == {
        "card-task", "asset-task-a", "asset-task-b",
    }


def test_task_ids_for_card_chunks_large_asset_sets(repo):
    asset_rows = [(f"asset-{index}", "card-many") for index in range(1001)]
    with repo._connect() as conn:
        conn.executemany("insert into card_assets(asset_id, card_id) values (?, ?)", asset_rows)
    repo.enqueue_many([
        Task(
            f"asset-task-{index}", TaskType.MEDIA_DOWNLOAD, "asset", f"asset-{index}"
        )
        for index in range(1001)
    ])

    assert repo.task_ids_for_card("card-many") == {
        f"asset-task-{index}" for index in range(1001)
    }


def test_enqueue_task(repo):
    task = Task(
        task_id="t1", task_type=TaskType.FETCH,
        target_kind="source", target_id="s1",
    )
    repo.enqueue(task)
    fetched = repo.get("t1")
    assert fetched is not None
    assert fetched.task_type == TaskType.FETCH
    assert fetched.status == TaskStatus.PENDING


def test_update_status(repo):
    task = Task(
        task_id="t1", task_type=TaskType.FETCH,
        target_kind="source", target_id="s1",
    )
    repo.enqueue(task)
    repo.update_status("t1", TaskStatus.RUNNING)
    assert repo.get("t1").status == TaskStatus.RUNNING


def test_update_status_with_error(repo):
    task = Task(
        task_id="t1", task_type=TaskType.FETCH,
        target_kind="source", target_id="s1",
    )
    repo.enqueue(task)
    repo.update_status("t1", TaskStatus.FAILED, error_reason="opencli_daemon_down")
    fetched = repo.get("t1")
    assert fetched.status == TaskStatus.FAILED
    assert fetched.error_reason == "opencli_daemon_down"


def test_success_status_clears_previous_error_reason(repo):
    task = Task(
        task_id="t1", task_type=TaskType.FETCH,
        target_kind="source", target_id="s1",
    )
    repo.enqueue(task)
    repo.update_status("t1", TaskStatus.FAILED, error_reason="fetch_rate_limited:min_gap")

    repo.update_status("t1", TaskStatus.SUCCESS)

    fetched = repo.get("t1")
    assert fetched.status == TaskStatus.SUCCESS
    assert fetched.error_reason is None


def test_list_pending(repo):
    repo.enqueue(Task(task_id="t1", task_type=TaskType.FETCH, target_kind="source", target_id="s1"))
    repo.enqueue(Task(task_id="t2", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card", target_id="c1"))
    repo.update_status("t1", TaskStatus.RUNNING)
    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].task_id == "t2"


def test_dependency_tracking(repo):
    repo.enqueue(Task(
        task_id="t1", task_type=TaskType.FETCH,
        target_kind="source", target_id="s1",
    ))
    repo.enqueue(Task(
        task_id="t2", task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card", target_id="c1",
        depends_on=(DependencySpec(task_id="t1", required_status=TaskStatus.SUCCESS),),
    ))
    # t2 不应在 t1 完成前进入 running
    repo.update_status("t1", TaskStatus.RUNNING)
    completed = {"t1": TaskStatus.RUNNING}
    assert not repo.get("t2").can_run(completed)
    completed["t1"] = TaskStatus.SUCCESS
    assert repo.get("t2").can_run(completed)


def test_list_due_pending_respects_next_run_at_and_dependencies(repo):
    repo.enqueue(Task(
        task_id="fetch_due",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="s1",
        next_run_at="2026-06-23T01:00:00Z",
    ))
    repo.enqueue(Task(
        task_id="fetch_later",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="s1",
        next_run_at="2026-06-24T01:00:00Z",
    ))
    repo.enqueue(Task(
        task_id="media_waiting",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="c1",
        next_run_at="2026-06-23T01:00:00Z",
        depends_on=(DependencySpec(task_id="fetch_due", required_status=TaskStatus.SUCCESS),),
    ))

    due = repo.list_due(now="2026-06-23T02:00:00Z")

    assert [task.task_id for task in due] == ["fetch_due"]

    repo.update_status("fetch_due", TaskStatus.SUCCESS)

    due_after_fetch = repo.list_due(now="2026-06-23T02:00:00Z")
    assert [task.task_id for task in due_after_fetch] == ["media_waiting"]


def test_list_due_accepts_required_blocked_dependency(repo):
    repo.enqueue(Task(
        task_id="media_blocked",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card_1",
        status=TaskStatus.BLOCKED,
    ))
    repo.enqueue(Task(
        task_id="context_after_blocked_media",
        task_type=TaskType.CONTEXT_PACK,
        target_kind="card",
        target_id="card_1",
        depends_on=(DependencySpec("media_blocked", TaskStatus.BLOCKED),),
    ))

    due = repo.list_due(now="2026-06-23T02:00:00Z")

    assert [task.task_id for task in due] == ["context_after_blocked_media"]


def test_list_due_can_filter_by_task_types(repo):
    repo.enqueue(Task(
        task_id="fetch_due",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="s1",
        next_run_at="2026-06-23T01:00:00Z",
    ))
    repo.enqueue(Task(
        task_id="enrich_due",
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id="card_1",
        next_run_at="2026-06-23T01:00:00Z",
    ))
    repo.enqueue(Task(
        task_id="summary_due",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card_1",
        next_run_at="2026-06-23T01:00:00Z",
    ))

    fetch_due = repo.list_due(now="2026-06-23T02:00:00Z", task_types=(TaskType.FETCH,))
    non_fetch_due = repo.list_due(
        now="2026-06-23T02:00:00Z",
        task_types=(TaskType.CONTENT_ENRICH, TaskType.SUMMARY),
    )

    assert [task.task_id for task in fetch_due] == ["fetch_due"]
    assert [task.task_id for task in non_fetch_due] == ["enrich_due", "summary_due"]


def test_list_due_prioritizes_maintenance_and_release_over_optional_backlog(repo):
    for index in range(3):
        repo.enqueue(Task(
            task_id=f"old-image-{index}",
            task_type=TaskType.IMAGE_UNDERSTAND,
            target_kind="asset",
            target_id=f"asset-{index}",
        ))
    repo.enqueue(Task(
        task_id="release-card",
        task_type=TaskType.CARD_FINALIZE,
        target_kind="card",
        target_id="card-1",
    ))
    repo.enqueue(Task(
        task_id="repair-pipeline",
        task_type=TaskType.PIPELINE_RECONCILE,
        target_kind="global",
        target_id="pipeline",
    ))

    assert [task.task_id for task in repo.list_due(limit=2)] == [
        "repair-pipeline",
        "release-card",
    ]


def test_list_due_filters_blocked_dependencies_and_fairly_advances_core_and_webhooks(repo):
    repo.enqueue(Task(
        task_id="blocked-repair",
        task_type=TaskType.PIPELINE_RECONCILE,
        target_kind="global",
        target_id="pipeline",
        depends_on=(DependencySpec("missing", TaskStatus.SUCCESS),),
    ))
    for index in range(60):
        repo.enqueue(Task(
            task_id=f"webhook-{index:02d}",
            task_type=TaskType.WEBHOOK_DELIVER,
            target_kind="card",
            target_id=f"card-{index:02d}",
        ))
    core_types = (
        TaskType.FETCH,
        TaskType.CARD_BUILD,
        TaskType.CONTENT_ENRICH,
        TaskType.MEDIA_DOWNLOAD,
    )
    for index, task_type in enumerate(core_types):
        repo.enqueue(Task(
            task_id=f"core-{index}",
            task_type=task_type,
            target_kind="card",
            target_id=f"core-{index}",
        ))

    snapshot = repo.list_due(limit=50)

    task_ids = {task.task_id for task in snapshot}
    assert "blocked-repair" not in task_ids
    assert {f"core-{index}" for index in range(4)} <= task_ids
    assert any(task.task_type is TaskType.WEBHOOK_DELIVER for task in snapshot)


def test_list_due_limit_one_uses_fifo_so_old_webhook_cannot_starve(repo):
    repo.enqueue(Task(
        task_id="core-before-webhook",
        task_type=TaskType.CARD_BUILD,
        target_kind="card",
        target_id="core-before-webhook",
    ))
    repo.enqueue(Task(
        task_id="old-webhook",
        task_type=TaskType.WEBHOOK_DELIVER,
        target_kind="card",
        target_id="old-webhook",
    ))

    selected: list[str] = []
    for index in range(8):
        repo.enqueue(Task(
            task_id=f"new-core-{index}",
            task_type=TaskType.CARD_BUILD,
            target_kind="card",
            target_id=f"new-core-{index}",
        ))
        due = repo.list_due(limit=1)
        selected.append(due[0].task_id)
        repo.update_status(due[0].task_id, TaskStatus.SUCCESS)
        if due[0].task_id == "old-webhook":
            break

    assert "old-webhook" in selected
    assert selected.index("old-webhook") <= 1


def test_artifacts_and_target_queries(repo):
    repo.enqueue(Task(task_id="t1", task_type=TaskType.VIDEO_EXTRACT_AUDIO, target_kind="asset", target_id="asset_1"))
    repo.update_artifacts("t1", {"audio_path": "data/audio/a.wav"})

    by_target = repo.list_by_target(target_kind="asset", target_id="asset_1")

    assert len(by_target) == 1
    assert by_target[0].artifacts_summary == {"audio_path": "data/audio/a.wav"}


def test_enqueue_maps_task_ownership_lease_and_version_metadata(repo):
    repo.enqueue(Task(
        task_id="t1",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="s1",
        claimed_by="worker-1",
        lease_expires_at="2026-07-10T01:01:00Z",
        heartbeat_at="2026-07-10T01:00:30Z",
        started_at="2026-07-10T01:00:00Z",
        finished_at="2026-07-10T01:02:00Z",
        input_version="v2",
        result_version="result-v2",
        last_failure_class="TransientFetchError",
    ))

    fetched = repo.get("t1")

    assert fetched is not None
    assert fetched.claimed_by == "worker-1"
    assert fetched.lease_expires_at == "2026-07-10T01:01:00Z"
    assert fetched.heartbeat_at == "2026-07-10T01:00:30Z"
    assert fetched.started_at == "2026-07-10T01:00:00Z"
    assert fetched.finished_at == "2026-07-10T01:02:00Z"
    assert fetched.input_version == "v2"
    assert fetched.result_version == "result-v2"
    assert fetched.last_failure_class == "TransientFetchError"


def test_enqueue_conflict_preserves_task_execution_state(repo):
    repo.enqueue(Task(
        task_id="t1",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="s1",
        status=TaskStatus.RUNNING,
        attempt=2,
        error_reason="previous-failure",
        claimed_by="worker-1",
        lease_expires_at="2026-07-10T01:01:00Z",
        heartbeat_at="2026-07-10T01:00:30Z",
        started_at="2026-07-10T01:00:00Z",
        finished_at="2026-07-10T01:02:00Z",
        input_version="v2",
        result_version="result-v2",
    ))

    repo.enqueue(Task(
        task_id="t1",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.PENDING,
        attempt=1,
        input_version="v3",
    ))

    fetched = repo.get("t1")

    assert fetched is not None
    assert fetched.status == TaskStatus.RUNNING
    assert fetched.claimed_by == "worker-1"
    assert fetched.lease_expires_at == "2026-07-10T01:01:00Z"
    assert fetched.heartbeat_at == "2026-07-10T01:00:30Z"
    assert fetched.started_at == "2026-07-10T01:00:00Z"
    assert fetched.finished_at == "2026-07-10T01:02:00Z"
    assert fetched.attempt == 2
    assert fetched.error_reason == "previous-failure"
    assert fetched.result_version == "result-v2"
    assert fetched.input_version == "v2"


def test_same_version_enqueue_preserves_success(repo):
    repo.enqueue(Task(
        task_id="summary_card-1_0123456789ab",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.SUCCESS,
        artifacts_summary={"result": "first"},
        input_version="0123456789abcdef",
        result_version="result-v1",
    ))

    repo.enqueue(Task(
        task_id="summary_card-1_0123456789ab",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        next_run_at="2026-07-11T00:00:00Z",
        artifacts_summary={"result": "replacement"},
        input_version="0123456789abcdef",
    ))

    task = repo.get("summary_card-1_0123456789ab")
    assert task is not None
    assert task.status == TaskStatus.SUCCESS
    assert task.artifacts_summary == {"result": "first"}
    assert task.next_run_at is None
    assert task.result_version == "result-v1"


def test_new_version_enqueue_creates_new_pending_task(repo):
    repo.enqueue(Task(
        task_id="summary_card-1_0123456789ab",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.SUCCESS,
        input_version="0123456789abcdef",
    ))
    repo.enqueue(Task(
        task_id="summary_card-1_fedcba987654",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        input_version="fedcba9876543210",
    ))

    first = repo.get("summary_card-1_0123456789ab")
    second = repo.get("summary_card-1_fedcba987654")
    assert first is not None and first.status == TaskStatus.SUCCESS
    assert second is not None and second.status == TaskStatus.PENDING


def test_reschedule_existing_is_an_explicit_state_change(repo):
    repo.enqueue(Task(
        task_id="failed-task",
        task_type=TaskType.SUMMARY,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.FAILED,
        attempt=3,
        error_reason="rate_limited",
    ))

    assert repo.reschedule_existing(
        "failed-task",
        next_run_at="2026-07-11T00:00:00Z",
        reset_attempts=True,
    ) is True

    task = repo.get("failed-task")
    assert task is not None
    assert task.status == TaskStatus.PENDING
    assert task.attempt == 1
    assert task.error_reason is None
    assert task.next_run_at == "2026-07-11T00:00:00Z"


def test_reschedule_existing_can_compare_and_swap_terminal_status(repo):
    repo.enqueue(Task(
        task_id="already-replayed",
        task_type=TaskType.WEBHOOK_DELIVER,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.PENDING,
    ))

    assert repo.reschedule_existing(
        "already-replayed",
        next_run_at="2026-07-11T00:00:00Z",
        reset_attempts=True,
        required_statuses=(TaskStatus.FAILED, TaskStatus.BLOCKED),
    ) is False
    assert repo.get("already-replayed").status is TaskStatus.PENDING


def test_claim_task_ids_skips_non_pending_tasks(repo):
    repo.enqueue(Task(task_id="pending", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    repo.enqueue(Task(task_id="running", task_type=TaskType.FETCH, target_kind="source", target_id="source-2"))
    repo.update_status("running", TaskStatus.RUNNING)

    claimed = repo.claim_task_ids(
        ["pending", "running"],
        worker_id="worker-1",
        now="2026-07-10T01:00:00Z",
        lease_expires_at="2026-07-10T01:05:00Z",
    )

    assert [task.task_id for task in claimed] == ["pending"]
    assert repo.get("running").claimed_by is None


def test_claim_task_ids_respects_dependencies(repo):
    repo.enqueue(Task(
        task_id="dependency",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
    ))
    repo.enqueue(Task(
        task_id="dependent",
        task_type=TaskType.MEDIA_DOWNLOAD,
        target_kind="card",
        target_id="card-1",
        depends_on=(DependencySpec("dependency", TaskStatus.SUCCESS),),
    ))

    blocked = repo.claim_task_ids(
        ["dependent"],
        worker_id="worker-1",
        now="2026-07-10T01:00:00Z",
        lease_expires_at="2026-07-10T01:05:00Z",
    )
    repo.update_status("dependency", TaskStatus.SUCCESS)
    claimable = repo.claim_task_ids(
        ["dependent"],
        worker_id="worker-1",
        now="2026-07-10T01:00:00Z",
        lease_expires_at="2026-07-10T01:05:00Z",
    )

    assert blocked == []
    assert [task.task_id for task in claimable] == ["dependent"]


def test_complete_requires_matching_worker_id(repo):
    repo.enqueue(Task(task_id="running", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    repo.claim_task_ids(
        ["running"],
        worker_id="worker-1",
        now="2026-07-10T01:00:00Z",
        lease_expires_at="2026-07-10T01:05:00Z",
    )

    unauthorized = repo.complete(
        "running", worker_id="worker-2", status=TaskStatus.SUCCESS,
        artifacts={"fetched": True}, error_reason=None, result_version="v2",
        finished_at="2026-07-10T01:02:00Z",
    )
    completed = repo.complete(
        "running", worker_id="worker-1", status=TaskStatus.SUCCESS,
        artifacts={"fetched": True}, error_reason=None, result_version="v2",
        finished_at="2026-07-10T01:02:00Z",
    )
    task = repo.get("running")

    assert unauthorized is False
    assert completed is True
    assert task.status == TaskStatus.SUCCESS
    assert task.artifacts_summary == {"fetched": True}
    assert task.claimed_by is None
    assert task.lease_expires_at is None
    assert task.heartbeat_at is None
    assert task.finished_at == "2026-07-10T01:02:00Z"


def test_completion_token_is_bound_to_one_terminal_attempt_and_cleared_on_requeue(repo):
    repo.enqueue(Task(task_id="completion-token", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    repo.claim_task_ids(
        ["completion-token"], worker_id="worker-1", now="2026-07-10T01:00:00Z",
        lease_expires_at="2026-07-10T01:05:00Z",
    )
    assert repo.complete(
        "completion-token", worker_id="worker-1", status=TaskStatus.SKIPPED,
        artifacts={"attempt": "first"}, error_reason="first skipped", result_version="v1",
        finished_at="2026-07-10T01:02:00Z", completion_token="first-completion",
    )
    completed = repo.get("completion-token")
    assert completed is not None
    assert completed.completion_token == "first-completion"

    repo.update_status("completion-token", TaskStatus.PENDING)
    requeued = repo.get("completion-token")
    assert requeued is not None
    assert requeued.completion_token is None

    claimed = repo.claim_task_ids(
        ["completion-token"], worker_id="worker-2", now="2026-07-10T01:03:00Z",
        lease_expires_at="2026-07-10T01:08:00Z",
    )
    assert [task.task_id for task in claimed] == ["completion-token"]
    assert claimed[0].completion_token is None


def test_heartbeat_requires_matching_worker_id(repo):
    repo.enqueue(Task(task_id="running", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    repo.claim_task_ids(
        ["running"],
        worker_id="worker-1",
        now="2026-07-10T01:00:00Z",
        lease_expires_at="2026-07-10T01:05:00Z",
    )

    unauthorized = repo.heartbeat(
        "running", worker_id="worker-2", now="2026-07-10T01:01:00Z",
        lease_expires_at="2026-07-10T01:06:00Z",
    )
    authorized = repo.heartbeat(
        "running", worker_id="worker-1", now="2026-07-10T01:01:00Z",
        lease_expires_at="2026-07-10T01:06:00Z",
    )
    task = repo.get("running")

    assert unauthorized is False
    assert authorized is True
    assert task.heartbeat_at == "2026-07-10T01:01:00Z"
    assert task.lease_expires_at == "2026-07-10T01:06:00Z"


def test_heartbeat_never_shortens_a_future_lease_when_clock_moves_back(repo):
    repo.enqueue(Task(task_id="future-lease", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    repo.claim_task_ids(
        ["future-lease"],
        worker_id="worker-1",
        now="2099-07-10T01:00:00Z",
        lease_expires_at="2099-07-10T01:05:00Z",
    )

    assert repo.heartbeat(
        "future-lease",
        worker_id="worker-1",
        now="2026-07-10T01:01:00Z",
        lease_expires_at="2026-07-10T01:06:00Z",
    )

    task = repo.get("future-lease")
    assert task is not None
    assert task.lease_expires_at == "2099-07-10T01:05:00Z"


def test_fail_or_retry_requires_owner_and_clears_lease(repo):
    from backend.application.tasks.retry import TaskRetryDecision

    repo.enqueue(Task(task_id="retrying", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    repo.claim_task_ids(
        ["retrying"], worker_id="worker-1", now="2026-07-10T00:00:00Z",
        lease_expires_at="2026-07-10T00:05:00Z",
    )
    decision = TaskRetryDecision(
        status=TaskStatus.PENDING,
        next_run_at="2026-07-10T00:00:30Z",
        failure_class="network_timeout",
        attempt=2,
    )

    unauthorized = repo.fail_or_retry(
        "retrying", worker_id="worker-2", decision=decision, error_reason="network timed out",
        updated_at="2026-07-10T00:00:00Z",
    )
    updated = repo.fail_or_retry(
        "retrying", worker_id="worker-1", decision=decision, error_reason="network timed out",
        updated_at="2026-07-10T00:00:00Z",
    )

    task = repo.get("retrying")
    assert unauthorized is False
    assert updated is True
    assert task.status == TaskStatus.PENDING
    assert task.attempt == 2
    assert task.next_run_at == "2026-07-10T00:00:30Z"
    assert task.claimed_by is None
    assert task.lease_expires_at is None
    assert task.heartbeat_at is None


def test_terminal_transition_rolls_back_when_event_append_is_busy(tmp_path: Path, monkeypatch) -> None:
    """A terminal state cannot commit without its durable event."""
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    initialize_runtime_storage(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(task_id="event-atomic", task_type=TaskType.FETCH, target_kind="source", target_id="source-1"))
    claimed = repo.claim_task_ids(
        ["event-atomic"], worker_id="worker-1", now="2026-07-10T00:00:00Z",
        lease_expires_at="2026-07-10T00:05:00Z",
    )
    assert [task.task_id for task in claimed] == ["event-atomic"]

    def busy_event_append(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(TaskEventRepository, "append_to_connection", busy_event_append)
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        repo.finalize_terminal_with_downstream(
            "event-atomic",
            worker_id="worker-1",
            status=TaskStatus.SUCCESS,
            artifacts={},
            error_reason=None,
            result_version="v1",
            finished_at="2026-07-10T00:01:00Z",
            enqueue_downstream=lambda _queue: [],
            event_chain_id="chain-1",
            event_payload={"attempt": 1},
        )

    task = repo.get("event-atomic")
    assert task is not None
    assert task.status is TaskStatus.RUNNING
    assert task.claimed_by == "worker-1"
    assert TaskEventRepository(tmp_path).list_events() == []


def test_retry_and_success_clear_stale_failure_class(tmp_path: Path) -> None:
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage

    initialize_runtime_storage(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="stale-failure-class",
        task_type=TaskType.CARD_LLM_ENRICH,
        target_kind="card",
        target_id="card-1",
        status=TaskStatus.FAILED,
        last_failure_class="MalformedResponse",
    ))

    assert repo.retry("stale-failure-class") is True
    assert repo.get("stale-failure-class").last_failure_class is None
    claimed = repo.claim_task_ids(
        ["stale-failure-class"],
        worker_id="worker-1",
        now="2026-07-23T00:00:00Z",
        lease_expires_at="2026-07-23T00:05:00Z",
    )
    assert len(claimed) == 1
    with repo._connect() as conn:
        conn.execute(
            "update tasks set last_failure_class = ? where task_id = ?",
            ("OldRetryFailure", "stale-failure-class"),
        )
    repo.finalize_terminal_with_downstream(
        "stale-failure-class",
        worker_id="worker-1",
        status=TaskStatus.SUCCESS,
        artifacts={},
        error_reason=None,
        result_version="v1",
        finished_at="2026-07-23T00:01:00Z",
        enqueue_downstream=lambda _queue: [],
    )
    assert repo.get("stale-failure-class").last_failure_class is None


def test_expired_lease_recovery_records_only_the_rows_it_actually_recovers(tmp_path: Path) -> None:
    from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
    initialize_runtime_storage(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(
        task_id="recover-once",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source-1",
        status=TaskStatus.RUNNING,
        claimed_by="dead-worker",
        lease_expires_at="2026-07-10T00:00:00Z",
        artifacts_summary={"chain_id": "chain-recovery"},
    ))

    assert repo.recover_expired_leases(
        "2026-07-10T00:01:00Z",
        event_occurred_at="2026-07-10T00:01:00Z",
    ) == 1
    # This models a second concurrent recovery pass that sees the row after it
    # was already returned to pending. It must not fabricate a recovery event.
    assert repo.recover_expired_leases(
        "2026-07-10T00:01:00Z",
        event_occurred_at="2026-07-10T00:01:00Z",
    ) == 0

    assert [event["event_type"] for event in TaskEventRepository(tmp_path).list_events()] == ["lease_recovered"]
