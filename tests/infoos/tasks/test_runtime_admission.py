from __future__ import annotations

from backend.application.tasks.admission import ResourceRequest
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.runtime_resource_leases import RuntimeResourceLeaseRepository


NOW = "2026-07-23T00:00:00Z"
EXPIRES = "2026-07-23T00:05:00Z"


def _pending(repo: TaskRepository, task_id: str) -> None:
    repo.enqueue(Task(task_id, TaskType.FETCH, "source", task_id))


def test_same_site_claims_at_most_one_and_busy_task_is_untouched(tmp_path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    _pending(repo, "x-one")
    _pending(repo, "x-two")

    admitted = repo.claim_task_ids_with_resources(
        ["x-one", "x-two"],
        worker_id="worker-a",
        now=NOW,
        lease_expires_at=EXPIRES,
        resource_requests={
            "x-one": ResourceRequest("browser_site", "twitter"),
            "x-two": ResourceRequest("browser_site", "twitter"),
        },
        event_occurred_at=NOW,
    )

    assert [item.task.task_id for item in admitted] == ["x-one"]
    busy = repo.get("x-two")
    assert busy is not None
    assert busy.status is TaskStatus.PENDING
    assert busy.attempt == 1
    assert busy.claimed_by is None
    assert busy.lease_expires_at is None
    assert busy.error_reason is None
    assert busy.last_failure_class is None


def test_different_sites_claim_in_parallel_and_owner_safe_release(tmp_path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    _pending(repo, "x")
    _pending(repo, "youtube")

    admitted = repo.claim_task_ids_with_resources(
        ["x", "youtube"],
        worker_id="worker-a",
        now=NOW,
        lease_expires_at=EXPIRES,
        resource_requests={
            "x": ResourceRequest("browser_site", "twitter"),
            "youtube": ResourceRequest("browser_site", "youtube"),
        },
    )

    assert {item.task.task_id for item in admitted} == {"x", "youtube"}
    leases = RuntimeResourceLeaseRepository(tmp_path)
    first = next(item for item in admitted if item.task.task_id == "x")
    assert not leases.release(
        "browser_site", "twitter", owner_token="replacement-owner"
    )
    assert leases.release(
        "browser_site", "twitter", owner_token=first.resource.owner_token
    )


def test_expired_resource_is_reclaimed_and_heartbeat_renews_owner_only(tmp_path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    leases = RuntimeResourceLeaseRepository(tmp_path)
    _pending(repo, "x")
    assert leases.acquire(
        "browser_site",
        "twitter",
        owner_token="dead-owner",
        task_id="dead-task",
        acquired_at="2026-07-22T23:00:00Z",
        lease_expires_at="2026-07-22T23:05:00Z",
    )

    admitted = repo.claim_task_ids_with_resources(
        ["x"],
        worker_id="worker-a",
        now=NOW,
        lease_expires_at=EXPIRES,
        resource_requests={"x": ResourceRequest("browser_site", "twitter")},
    )
    assert len(admitted) == 1
    owner = admitted[0].resource.owner_token
    assert not leases.renew(
        "browser_site",
        "twitter",
        owner_token="dead-owner",
        updated_at="2026-07-23T00:01:00Z",
        lease_expires_at="2026-07-23T00:06:00Z",
    )
    assert leases.renew(
        "browser_site",
        "twitter",
        owner_token=owner,
        updated_at="2026-07-23T00:01:00Z",
        lease_expires_at="2026-07-23T00:06:00Z",
    )


def test_task_and_resource_heartbeat_renew_atomically(tmp_path) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    _pending(repo, "x")
    admitted = repo.claim_task_ids_with_resources(
        ["x"],
        worker_id="worker-a",
        now=NOW,
        lease_expires_at=EXPIRES,
        resource_requests={"x": ResourceRequest("browser_site", "twitter")},
    )
    resource = admitted[0].resource

    assert repo.heartbeat_with_resource(
        "x",
        worker_id="worker-a",
        resource=resource,
        now="2026-07-23T00:01:00Z",
        lease_expires_at="2026-07-23T00:06:00Z",
    )
    assert not repo.heartbeat_with_resource(
        "x",
        worker_id="wrong-worker",
        resource=resource,
        now="2026-07-23T00:02:00Z",
        lease_expires_at="2026-07-23T00:07:00Z",
    )


def test_terminal_commit_rejects_resource_reclaimed_after_handler_check(
    tmp_path,
) -> None:
    repo = TaskRepository.for_base_dir(tmp_path)
    _pending(repo, "x-race")
    admitted = repo.claim_task_ids_with_resources(
        ["x-race"],
        worker_id="worker-a",
        now=NOW,
        lease_expires_at=EXPIRES,
        resource_requests={
            "x-race": ResourceRequest("browser_site", "twitter"),
        },
    )
    resource = admitted[0].resource
    with repo._connect() as conn:
        conn.execute(
            """
            update runtime_resource_leases
            set owner_token = ?, updated_at = ?
            where resource_kind = ? and resource_key = ?
            """,
            (
                "replacement-owner",
                "2026-07-23T00:04:59Z",
                resource.resource_kind,
                resource.resource_key,
            ),
        )
    downstream_called = False

    def enqueue_downstream(_queue):
        nonlocal downstream_called
        downstream_called = True
        return ["must-not-exist"]

    finalized = repo.finalize_terminal_with_downstream(
        "x-race",
        worker_id="worker-a",
        status=TaskStatus.SUCCESS,
        artifacts={},
        error_reason=None,
        result_version="v1",
        finished_at="2026-07-23T00:05:00Z",
        enqueue_downstream=enqueue_downstream,
        required_resource_owner=(
            resource.resource_kind,
            resource.resource_key,
            resource.owner_token,
        ),
    )

    persisted = repo.get("x-race")
    assert finalized is None
    assert downstream_called is False
    assert persisted is not None
    assert persisted.status is TaskStatus.RUNNING
    assert persisted.claimed_by == "worker-a"
