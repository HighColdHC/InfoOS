from __future__ import annotations

from pathlib import Path

from backend.application.tasks.types import Task, TaskType
from backend.application.tasks.registry import handler_for


def test_pipeline_reconcile_handler_runs_the_reconciliation_workflow(tmp_path: Path, monkeypatch):
    expected = {"scanned": 3, "created_by_type": {"card_finalize": 1}, "batch_links_repaired": 0, "errors": []}
    monkeypatch.setattr(
        "backend.application.workflows.pipeline_reconciliation.reconcile_pipeline",
        lambda base_dir: expected,
    )
    monkeypatch.setattr(
        "backend.application.workflows.audio_retention.cleanup_retained_audio",
        lambda base_dir: {
            "enabled": True,
            "retention_days": 7,
            "scanned": 0,
            "deleted": 0,
            "missing": 0,
            "skipped": 0,
            "bytes_reclaimed": 0,
            "errors": [],
        },
    )
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.drain_auth_recovery_notifications",
        lambda base_dir: {"claimed": 0, "sent": 0, "failed": 0, "skipped": 0},
    )
    task = Task(
        task_id="pipeline_reconcile_hour",
        task_type=TaskType.PIPELINE_RECONCILE,
        target_kind="global",
        target_id="pipeline",
    )

    result = handler_for(TaskType.PIPELINE_RECONCILE)(task, tmp_path)

    assert result == {
        "status": "success",
        "artifacts": {
            **expected,
            "audio_retention": {
                "enabled": True,
                "retention_days": 7,
                "scanned": 0,
                "deleted": 0,
                "missing": 0,
                "skipped": 0,
                "bytes_reclaimed": 0,
                "errors": [],
            },
            "auth_recovery_notifications": {
                "claimed": 0,
                "sent": 0,
                "failed": 0,
                "skipped": 0,
            },
        },
    }
