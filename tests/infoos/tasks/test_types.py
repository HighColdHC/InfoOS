"""Task types and statuses (spec §1)。"""
from __future__ import annotations


from backend.application.tasks.types import TaskType, TaskStatus, Task
from backend.application.tasks.queues import LOCAL_IO_QUEUE_TASK_TYPES, MAINTENANCE_QUEUE_TASK_TYPES, SEMANTIC_QUEUE_TASK_TYPES


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
