from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.repository import TaskRepository
from backend.application.usecases.task_cancel import cancel_task
from backend.application.usecases.task_batch_cancel import cancel_tasks
from backend.domain.tasks import Task, TaskStatus, TaskType
from backend.infrastructure.storage.task_events import TaskEventRepository
from backend.read_models.source_run_trace_safety import task_identifier_alias


def _task(status=TaskStatus.PENDING, task_id="cancel_me") -> Task:
    return Task(
        task_id=task_id, task_type=TaskType.CONTENT_ENRICH,
        target_kind="card", target_id="missing_card", status=status,
        attempt=3, max_attempts=3, next_run_at="2099-01-01T00:00:00Z",
        error_reason="browser_session_not_ready", last_failure_class="auth_required",
        artifacts_summary={"chain_id": "cancel_chain", "evidence": "preserve"},
    )


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.BLOCKED])
def test_cancel_records_terminal_state_reason_and_preserves_history(tmp_path, status):
    repo = TaskRepository.for_base_dir(tmp_path)
    before = _task(status)
    repo.enqueue(before)
    events = TaskEventRepository(tmp_path)
    events.append(task_id=before.task_id, event_type="blocked", payload={"original": True})

    result = cancel_task(tmp_path, before.task_id, reason="目标卡已被后续成功覆盖")

    assert result == {
        "ok": True, "task_id": before.task_id, "status": "skipped", "reason": "目标卡已被后续成功覆盖",
    }
    after = repo.get(before.task_id)
    assert after.status == TaskStatus.SKIPPED
    assert after.attempt == before.attempt
    assert after.last_failure_class == before.last_failure_class
    assert after.error_reason == "operator_cancelled"
    assert after.artifacts_summary == {
        **before.artifacts_summary, "cancellation": {"reason": result["reason"]},
    }
    assert after.next_run_at is None
    assert after.finished_at == after.updated_at
    assert after.finished_at is not None
    assert after.claimed_by is after.lease_expires_at is after.heartbeat_at is after.completion_token is None
    history = events.list_events(task_id=before.task_id)
    assert [event["event_type"] for event in history] == ["blocked", "cancelled"]
    cancelled = history[-1]
    assert cancelled["chain_id"] == "cancel_chain"
    assert cancelled["status"] == "skipped"
    assert cancelled["occurred_at"] == after.finished_at
    assert cancelled["payload"] == {
        "reason": result["reason"], "previous_status": status.value, "trigger": "manual",
        "previous_error_reason": before.error_reason, "previous_failure_class": before.last_failure_class,
        "attempt": 3,
    }


@pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.SKIPPED])
def test_cancel_rejects_running_and_terminal_tasks_without_changes(tmp_path, status):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_task(status))
    before = repo.get("cancel_me")

    result = cancel_task(tmp_path, "cancel_me", reason="不再需要")

    assert result == {"ok": False, "task_id": "cancel_me", "code": "task_not_cancellable", "status_code": 409}
    assert repo.get("cancel_me") == before
    assert TaskEventRepository(tmp_path).list_events(task_id="cancel_me") == []


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": " \n\t "}, {"reason": None}])
@pytest.mark.parametrize("batch", [False, True])
def test_cancel_http_requires_nonempty_reason(tmp_path, body, batch):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_task())
    url = "/api/tasks/cancel" if batch else "/api/tasks/cancel_me/cancel"
    if batch:
        body = {**body, "task_ids": ["cancel_me"]}

    response = TestClient(create_app(tmp_path)).post(url, json=body)

    assert response.status_code == 422
    assert repo.get("cancel_me").status == TaskStatus.PENDING
    assert TaskEventRepository(tmp_path).list_events(task_id="cancel_me") == []


def test_cancel_http_resolves_trace_alias_and_exposes_reason_in_detail(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_task(TaskStatus.BLOCKED))
    client = TestClient(create_app(tmp_path))

    response = client.post(
        f"/api/tasks/{task_identifier_alias('cancel_me')}/cancel", json={"reason": "  孤儿任务  "},
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "孤儿任务"
    detail = client.get("/api/tasks/cancel_me").json()["task"]
    assert detail["status"] == "skipped"
    assert detail["artifacts_summary"]["cancellation"] == {"reason": "孤儿任务"}
    assert client.post("/api/tasks/cancel_me/cancel", json={"reason": "重复"}).status_code == 409
    missing = client.post("/api/tasks/missing/cancel", json={"reason": "不存在"})
    assert missing.status_code == 404
    assert missing.json()["code"] == "task_not_found"


def test_batch_cancel_returns_each_business_result(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([_task(task_id="one"), _task(TaskStatus.RUNNING, "running"), _task(TaskStatus.BLOCKED, "two")])

    response = TestClient(create_app(tmp_path)).post("/api/tasks/cancel", json={
        "task_ids": ["one", "running", "missing", task_identifier_alias("two")], "reason": "积压清理",
    })

    assert response.status_code == 200
    results = response.json()["results"]
    assert [item["task_id"] for item in results] == ["one", "running", "missing", "two"]
    assert [item["ok"] for item in results] == [True, False, False, True]
    assert results[1]["code"] == "task_not_cancellable"
    assert results[2]["code"] == "task_not_found"
    assert repo.get("one").status == repo.get("two").status == TaskStatus.SKIPPED
    assert repo.get("running").status == TaskStatus.RUNNING


@pytest.mark.parametrize("count", [0, 101])
def test_batch_cancel_rejects_out_of_bounds_request(tmp_path, count):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(_task())
    response = TestClient(create_app(tmp_path)).post("/api/tasks/cancel", json={
        "task_ids": ["cancel_me"] * count, "reason": "积压清理",
    })
    assert response.status_code == 422
    assert repo.get("cancel_me").status == TaskStatus.PENDING


def test_batch_event_failure_rolls_back_one_task_and_continues(tmp_path, monkeypatch):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue_many([_task(task_id="one"), _task(task_id="two")])
    before = repo.get("one")
    append = TaskEventRepository.append_to_connection

    def fail_first(conn, **kwargs):
        if kwargs["task_id"] == "one":
            raise sqlite3.OperationalError("event write failed")
        return append(conn, **kwargs)

    monkeypatch.setattr(TaskEventRepository, "append_to_connection", fail_first)
    results = cancel_tasks(tmp_path, ["one", "two"], reason="独立事务")["results"]

    assert results[0]["code"] == "task_cancel_failed"
    assert results[1]["ok"] is True
    assert repo.get("one") == before
    assert repo.get("two").status == TaskStatus.SKIPPED
    assert TaskEventRepository(tmp_path).list_events(task_id="one") == []
    assert len(TaskEventRepository(tmp_path).list_events(task_id="two")) == 1


@pytest.mark.parametrize("url,body", [
    ("/api/tasks/cancel_me/cancel", {"reason": "取消"}),
    ("/api/tasks/cancel", {"task_ids": ["cancel_me"], "reason": "取消"}),
])
def test_cancel_uses_management_same_origin_protection(tmp_path, url, body):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(replace(_task(), error_reason=None))
    response = TestClient(create_app(tmp_path)).post(url, json=body, headers={"origin": "https://other.example"})
    assert response.status_code == 403
    assert repo.get("cancel_me").status == TaskStatus.PENDING
