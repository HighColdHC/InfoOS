import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.workers.scheduler.cycle import run_scheduler_cycle


def test_scheduler_cycle_plans_fetch_without_executing_handlers(tmp_path, monkeypatch):
    calls = []

    def fake_plan_sources(base_dir, *, now):
        calls.append((base_dir, now))
        return ["fetch_rss_ai_1"]

    monkeypatch.setattr("backend.workers.scheduler.cycle.now_utc_iso", lambda: "2026-07-10T08:00:00Z")
    monkeypatch.setattr(
        "backend.workers.scheduler.cycle.enqueue_fetch_tasks_for_enabled_sources",
        fake_plan_sources,
    )
    # If the scheduler re-acquires a handler execution path, this test fails
    # at import/lookup rather than silently accepting it.
    monkeypatch.setattr(
        "backend.application.tasks.worker.run_due_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("scheduler executed tasks")),
    )

    result = run_scheduler_cycle(base_dir=tmp_path)

    assert calls == [(tmp_path, "2026-07-10T08:00:00Z")]
    assert result["status"] == "success"
    assert result["sources"] == {
        "status": "planned",
        "planned_task_ids": ["fetch_rss_ai_1"],
        "planned_count": 1,
    }
    assert result["task_pipeline"] == {"status": "planned", "reason": "task worker owns execution"}
    assert result["scheduled"] == {"collector_only": True}
    assert result["maintenance"]["status"] == "planned"
    assert result["maintenance"]["planned_count"] == 3
    assert result["queue"]["pending"] == 3


def test_scheduler_cycle_does_not_enqueue_retired_downstream_products(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.workers.scheduler.cycle.now_utc_iso", lambda: "2026-07-10T08:00:00Z")
    monkeypatch.setattr(
        "backend.workers.scheduler.cycle.enqueue_fetch_tasks_for_enabled_sources",
        lambda *args, **kwargs: [],
    )

    first = run_scheduler_cycle(base_dir=tmp_path)
    second = run_scheduler_cycle(base_dir=tmp_path)

    assert first["scheduled"] == {"collector_only": True}
    assert second["scheduled"] == {"collector_only": True}
    assert first["queue"]["by_task_type"] == {"pipeline_reconcile": 1, "task_event_archive": 1, "daily_brief_build": 1}
    assert second["queue"]["by_task_type"] == {"pipeline_reconcile": 1, "task_event_archive": 1, "daily_brief_build": 1}
    assert first["queue"]["total"] == 3
    assert second["queue"]["total"] == 3


def test_archive_plan_still_runs_when_other_maintenance_plan_fails(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.workers.scheduler.cycle.now_utc_iso", lambda: "2026-07-10T08:00:00Z")
    monkeypatch.setattr(
        "backend.workers.scheduler.cycle.enqueue_fetch_tasks_for_enabled_sources",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "backend.workers.scheduler.cycle.enqueue_pipeline_reconciliation_task",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("reconcile unavailable")),
    )
    result = run_scheduler_cycle(base_dir=tmp_path)
    assert result["status"] == "partial_failure"
    assert result["maintenance"]["planned_count"] == 2
    assert result["queue"]["by_task_type"] == {"task_event_archive": 1, "daily_brief_build": 1}
