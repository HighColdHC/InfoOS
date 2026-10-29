"""Plan D 端到端冒烟：task 依赖图 + schema 集成。"""
from __future__ import annotations


from backend.application.tasks import Task, TaskType, TaskStatus
from backend.application.tasks.types import DependencySpec
from backend.domain.failures import FetchFailureReason


def test_task_dag_fetch_to_summary():
    """fetch → summary 的 DAG 应正确判定依赖。"""
    fetch = Task(task_id="t1", task_type=TaskType.FETCH,
                 target_kind="source", target_id="s1")
    summary = Task(
        task_id="t2", task_type=TaskType.SUMMARY,
        target_kind="card", target_id="c1",
        depends_on=(DependencySpec(task_id="t1", required_status=TaskStatus.SUCCESS),),
    )
    completed = {"t1": TaskStatus.SUCCESS}
    assert summary.can_run(completed)
    completed["t1"] = TaskStatus.FAILED
    assert not summary.can_run(completed)


def test_failure_reason_integrates_with_task():
    """Plan A 的 FetchFailureReason 与 Plan D 的 Task.error_reason 互通。"""
    reason = FetchFailureReason.OPENCLI_DAEMON_DOWN
    task = Task(
        task_id="t1", task_type=TaskType.FETCH,
        target_kind="source", target_id="s1",
        status=TaskStatus.FAILED,
        error_reason=reason.value,
    )
    assert task.error_reason == "opencli_daemon_down"
