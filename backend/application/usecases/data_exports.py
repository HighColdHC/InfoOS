"""Submit and estimate task-backed data exports."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from backend.application.tasks.repository import TaskRepository
from backend.domain.export_contracts import export_request_input_version, normalize_export_request
from backend.domain.tasks import Task, TaskType
from backend.infrastructure.storage.database import ensure_database_initialized
from backend.infrastructure.storage.export_queries import ExportQueries
from backend.infrastructure.storage.paths import resolve_sqlite_path


def estimate_export(base_dir: Path | str, payload: dict[str, object]) -> dict[str, object]:
    request = normalize_export_request(payload)
    estimate = ExportQueries(base_dir).estimate(request)
    return {"normalized_request": request, **estimate}


def submit_export(base_dir: Path | str, payload: dict[str, object]) -> dict[str, str]:
    request = normalize_export_request(payload)
    ensure_database_initialized(resolve_sqlite_path(base_dir))
    export_id = f"export_{uuid4().hex}"
    input_version = export_request_input_version(request)
    task_id = f"data_export_{export_id}_{input_version.rsplit(':', 1)[-1][:16]}"
    task = Task(
        task_id=task_id,
        task_type=TaskType.DATA_EXPORT,
        target_kind="export",
        target_id=export_id,
        max_attempts=2,
        input_version=input_version,
        artifacts_summary={
            "export_id": export_id,
            "kind": request["kind"],
            "request": request,
            "progress_percent": 0,
        },
    )
    TaskRepository.for_base_dir(base_dir).enqueue(task)
    return {
        "task_id": task_id,
        "export_id": export_id,
        "status": "pending",
        "progress_url": f"/api/exports/{task_id}",
    }
