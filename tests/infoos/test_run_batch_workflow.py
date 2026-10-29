from __future__ import annotations

from pathlib import Path


from backend.application.tasks.types import Task, TaskType
from backend.application.workflows import source_batch


def test_run_routes_do_not_own_task_planning_or_batch_submission():
    route_source = Path("backend/api/routes/runs.py").read_text(encoding="utf-8")

    assert "plan_fetch_tasks_for_enabled_sources" not in route_source
    assert "pending_due_fetch_task_ids_for_enabled_sources" not in route_source
    assert "submit_task_batch" not in route_source


def test_source_batch_workflow_unifies_planning_pending_dedup_and_submission(tmp_path, monkeypatch):
    planned = Task(
        task_id="fetch_new",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="source_new",
    )
    force_flags: list[bool] = []
    submissions: list[dict[str, object]] = []

    def fake_plan(base_dir, *, now, horizon_hours, plan_followup_fetch, force_now):
        force_flags.append(force_now)
        return [planned]

    monkeypatch.setattr(source_batch, "plan_fetch_tasks_for_enabled_sources", fake_plan)
    monkeypatch.setattr(
        source_batch,
        "pending_due_fetch_task_ids_for_enabled_sources",
        lambda base_dir, *, now: ["fetch_new", "fetch_existing", "fetch_existing"],
    )
    monkeypatch.setattr(
        source_batch,
        "submit_task_batch",
        lambda base_dir, **kwargs: submissions.append(kwargs) or {"batch_id": kwargs["batch_type"]},
    )

    forced = source_batch.enqueue_source_batch(tmp_path, now="2026-07-12T00:00:00Z")

    assert forced == {"batch_id": "source_run"}
    assert force_flags == [True]
    assert [submission["batch_type"] for submission in submissions] == ["source_run"]
    assert all(submission["root_tasks"] == [planned] for submission in submissions)
    assert all(submission["existing_task_ids"] == ["fetch_existing"] for submission in submissions)
    assert all(submission["metadata"] == {"planned_task_count": 2, "planned_task_ids": ["fetch_new", "fetch_existing"], "limit": 50, "source_count": 0} for submission in submissions)
