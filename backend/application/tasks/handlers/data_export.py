"""Persistent task handler for atomic export artifact production."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.workflows.data_export import artifact_matches, build_export_artifact
from backend.domain.export_contracts import normalize_export_request
from backend.domain.tasks import Task, TaskStatus


def handle_data_export(task: Task, base_dir: Path) -> dict[str, Any]:
    summary = task.artifacts_summary
    export_id = str(summary.get("export_id") or task.target_id)
    request_payload = summary.get("request")
    if not isinstance(request_payload, dict):
        return {
            "status": TaskStatus.FAILED.value,
            "error": "export_request_missing",
            "artifacts": {"export_id": export_id, "progress_percent": 100},
        }
    request = normalize_export_request(request_payload)
    existing = summary.get("artifact")
    if isinstance(existing, dict) and artifact_matches(base_dir, existing):
        artifact = dict(existing)
    else:
        artifact = build_export_artifact(base_dir, export_id=export_id, request=request)
    artifact["download_url"] = f"/api/exports/{task.task_id}/download"
    return {
        "status": TaskStatus.SUCCESS.value,
        "result_version": task.input_version,
        "artifacts": {
            "export_id": export_id,
            "kind": request["kind"],
            "request": request,
            "progress_percent": 100,
            "artifact": artifact,
        },
    }
