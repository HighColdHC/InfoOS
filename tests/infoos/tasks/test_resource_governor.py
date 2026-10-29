from __future__ import annotations

from backend.application.tasks.admission import ResourceRequest
from backend.application.tasks.resource_governor import resource_plan_for
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.runtime.resource_pressure import RuntimePressureSnapshot
from backend.infrastructure.storage.runtime_resource_leases import (
    RuntimeResourceLeaseRepository,
)


def _task(task_id: str, task_type: TaskType) -> Task:
    return Task(task_id, task_type, "asset", f"asset-{task_id}")


def test_media_compute_tasks_share_one_configurable_resource_pool(monkeypatch) -> None:
    monkeypatch.setenv("INFOOS_MEDIA_COMPUTE_SLOTS", "2")
    normal = RuntimePressureSnapshot(
        memory_full_avg10=0.0,
        mem_available_bytes=20 * 1024**3,
        cgroup_current_bytes=6 * 1024**3,
    )

    plans = [
        resource_plan_for(_task("audio", TaskType.VIDEO_EXTRACT_AUDIO), snapshot=normal),
        resource_plan_for(_task("asr", TaskType.VIDEO_TRANSCRIBE), snapshot=normal),
    ]

    assert all(plan.allowed for plan in plans)
    assert all(isinstance(plan.resource_request, ResourceRequest) for plan in plans)
    assert {
        plan.resource_request.candidate_keys
        for plan in plans
        if plan.resource_request is not None
    } == {("slot:0", "slot:1")}


def test_pressure_defers_new_media_compute_without_blocking_light_tasks() -> None:
    pressured = RuntimePressureSnapshot(
        memory_full_avg10=8.12,
        mem_available_bytes=23 * 1024**3,
        cgroup_current_bytes=6 * 1024**3,
    )

    media = resource_plan_for(
        _task("audio", TaskType.VIDEO_EXTRACT_AUDIO),
        snapshot=pressured,
    )
    light = resource_plan_for(
        Task("finalize", TaskType.CARD_FINALIZE, "card", "card-1"),
        snapshot=pressured,
    )

    assert media.allowed is False
    assert media.defer_reason == "memory_full_pressure"
    assert light.allowed is True
    assert light.resource_request is None


def test_resource_pool_claims_are_atomic_across_worker_processes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("INFOOS_MEDIA_COMPUTE_SLOTS", "2")
    repo = TaskRepository.for_base_dir(tmp_path)
    tasks = [
        _task("audio-second", TaskType.VIDEO_EXTRACT_AUDIO),
        _task("audio", TaskType.VIDEO_EXTRACT_AUDIO),
        _task("asr", TaskType.VIDEO_TRANSCRIBE),
    ]
    for task in tasks:
        repo.enqueue(task)
    snapshot = RuntimePressureSnapshot(
        memory_full_avg10=0.0,
        mem_available_bytes=20 * 1024**3,
        cgroup_current_bytes=6 * 1024**3,
    )
    requests = {
        task.task_id: resource_plan_for(
            task,
            snapshot=snapshot,
        ).resource_request
        for task in tasks
    }

    admitted = repo.claim_task_ids_with_resources(
        [task.task_id for task in tasks],
        worker_id="worker-a",
        now="2026-07-31T00:00:00Z",
        lease_expires_at="2026-07-31T00:05:00Z",
        resource_requests={
            task_id: request
            for task_id, request in requests.items()
            if request is not None
        },
    )

    assert len(admitted) == 2
    assert {item.resource.resource_key for item in admitted} == {"slot:0", "slot:1"}
    deferred = repo.get("asr")
    assert deferred is not None
    assert deferred.status is TaskStatus.PENDING

    released = admitted[0].resource
    assert RuntimeResourceLeaseRepository(tmp_path).release(
        released.resource_kind,
        released.resource_key,
        owner_token=released.owner_token,
    )
    repo.finalize_terminal_with_downstream(
        admitted[0].task.task_id,
        worker_id="worker-a",
        status=TaskStatus.SUCCESS,
        artifacts={},
        error_reason=None,
        result_version=None,
        finished_at="2026-07-31T00:00:01Z",
        enqueue_downstream=lambda _repo: [],
    )

    retry_request = requests["asr"]
    assert retry_request is not None
    retried = repo.claim_task_ids_with_resources(
        ["asr"],
        worker_id="worker-b",
        now="2026-07-31T00:00:02Z",
        lease_expires_at="2026-07-31T00:05:02Z",
        resource_requests={"asr": retry_request},
    )

    assert len(retried) == 1
