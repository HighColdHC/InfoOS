from __future__ import annotations

from pathlib import Path

from backend.application.usecases.data_exports import estimate_export, submit_export
from backend.application.tasks.executor import run_tasks_by_ids
from backend.domain.export_contracts import export_request_input_version
from tests.infoos.storage.migration_test_helpers import migrate_database_path
from backend.infrastructure.storage.export_jobs import get_export_job, list_export_jobs


def _runtime(tmp_path: Path) -> Path:
    migrate_database_path(tmp_path / "data" / "infoos.sqlite3")
    return tmp_path


def test_estimate_is_read_only_and_returns_normalized_scope(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)

    estimate = estimate_export(
        base_dir,
        {"kind": "data", "include_cards_content": True, "include_attachment_files": True},
    )

    assert estimate["normalized_request"]["format"] == "zip"
    assert estimate["estimated_bytes"] >= 0
    assert estimate["file_count"] == 1
    assert estimate["media_bytes"] == 0
    assert list_export_jobs(base_dir, limit=20, offset=0)["total"] == 0


def test_submit_creates_data_export_task_and_history_projects_from_tasks(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)

    submitted = submit_export(
        base_dir,
        {"kind": "data", "format": "json", "include_cards_content": True},
    )
    detail = get_export_job(base_dir, submitted["task_id"])
    page = list_export_jobs(base_dir, limit=20, offset=0)

    assert submitted == {
        "task_id": submitted["task_id"],
        "export_id": submitted["export_id"],
        "status": "pending",
        "progress_url": f"/api/exports/{submitted['task_id']}",
    }
    assert detail is not None
    assert detail["kind"] == "data"
    assert detail["status"] == "pending"
    assert detail["progress_percent"] == 0
    assert detail["artifact"] is None
    assert page["items"] == [detail]
    assert page["total"] == 1
    expected_version = export_request_input_version(detail["request"])
    assert submitted["task_id"] == (
        f"data_export_{submitted['export_id']}_{expected_version.rsplit(':', 1)[-1][:16]}"
    )


def test_submitted_export_runs_through_registered_worker_handler(tmp_path: Path) -> None:
    base_dir = _runtime(tmp_path)
    submitted = submit_export(
        base_dir, {"kind": "data", "format": "json", "include_cards_content": True}
    )

    result = run_tasks_by_ids(base_dir, [submitted["task_id"]])
    detail = get_export_job(base_dir, submitted["task_id"])

    assert result["succeeded"] == 1
    assert detail is not None and detail["status"] == "completed"
    assert detail["artifact"] is not None
    assert (base_dir / "data" / detail["artifact"]["logical_location"]).is_file()
