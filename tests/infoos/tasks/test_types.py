"""Task types and statuses (spec §1)。"""
from __future__ import annotations


from backend.application.tasks.types import TaskType, TaskStatus, Task, DependencySpec
from backend.application.tasks.queues import LOCAL_IO_QUEUE_TASK_TYPES, MAINTENANCE_QUEUE_TASK_TYPES, SEMANTIC_QUEUE_TASK_TYPES


def test_task_types_cover_fetch_enrichment_media_and_summary_pipeline():
    expected = {
        "source_calibrate",
        "fetch",
        "card_build",
        "content_enrich",
        "media_download",
        "image_understand",
        "video_extract_audio",
        "video_transcribe",
        "card_finalize",
        "daily_brief_build",
        "card_llm_enrich",
        "webhook_deliver",
        "notification_deliver",
        "auth_qr_capture",
        "auth_qr_check",
        "data_export",
        "context_pack",
        "summary",
        "semantic_extract",
        "score",
        "decide",
        "output_generate",
        "topic_update",
        "action_execute",
        "pipeline_reconcile",
        "task_event_archive",
        "cluster",
        "daily_brief",
    }
    from backend.domain.tasks import RETIRED_TASK_TYPES
    expected.update(t.value for t in RETIRED_TASK_TYPES)
    actual = {t.value for t in TaskType}
    assert actual == expected


def test_six_task_statuses():
    expected = {"pending", "running", "success", "failed", "blocked", "skipped"}
    actual = {s.value for s in TaskStatus}
    assert actual == expected


def test_pipeline_reconcile_is_maintenance_work_not_semantic_work():
    assert TaskType.PIPELINE_RECONCILE in MAINTENANCE_QUEUE_TASK_TYPES
    assert TaskType.PIPELINE_RECONCILE not in SEMANTIC_QUEUE_TASK_TYPES
    assert TaskType.TASK_EVENT_ARCHIVE in MAINTENANCE_QUEUE_TASK_TYPES


def test_data_export_is_serial_local_io_work():
    assert LOCAL_IO_QUEUE_TASK_TYPES == (TaskType.DATA_EXPORT,)
    assert TaskType.DATA_EXPORT not in SEMANTIC_QUEUE_TASK_TYPES


def test_action_execute_is_semantic_action_work():
    assert TaskType.ACTION_EXECUTE in SEMANTIC_QUEUE_TASK_TYPES


def test_task_dataclass_minimal():
    t = Task(
        task_id="t1",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="s1",
        status=TaskStatus.PENDING,
    )
    assert t.task_id == "t1"
    assert t.task_type == TaskType.FETCH
    assert t.status == TaskStatus.PENDING
    assert t.depends_on == ()  # default
    assert t.attempt == 1
    assert t.max_attempts == 3
    assert t.claimed_by is None
    assert t.lease_expires_at is None
    assert t.heartbeat_at is None
    assert t.started_at is None
    assert t.finished_at is None
    assert t.input_version == "v1"
    assert t.result_version is None
    assert t.last_failure_class is None
    assert t.completion_token is None


def test_task_dataclass_exposes_ownership_lease_and_version_metadata():
    task = Task(
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
        completion_token="completion-v2",
    )

    assert task.claimed_by == "worker-1"
    assert task.lease_expires_at == "2026-07-10T01:01:00Z"
    assert task.heartbeat_at == "2026-07-10T01:00:30Z"
    assert task.started_at == "2026-07-10T01:00:00Z"
    assert task.finished_at == "2026-07-10T01:02:00Z"
    assert task.input_version == "v2"
    assert task.result_version == "result-v2"
    assert task.last_failure_class == "TransientFetchError"
    assert task.completion_token == "completion-v2"


def test_dependency_spec_dataclass():
    dep = DependencySpec(
        task_id="t2",
        required_status=TaskStatus.SUCCESS,
    )
    assert dep.task_id == "t2"
    assert dep.required_status == TaskStatus.SUCCESS
