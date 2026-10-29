"""Task-backed export history and artifact ownership checks."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import TypedDict, cast
import sqlite3

from backend.domain.json_types import JsonObject

from backend.infrastructure.security.redaction import redact_text
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_data_dir, resolve_sqlite_path


class ExportTaskRow(TypedDict):
    task_id: str
    task_type: str
    target_kind: str
    target_id: str
    status: str
    depends_on: str
    attempt: int
    max_attempts: int
    next_run_at: str | None
    error_reason: str | None
    artifacts_summary: str
    claimed_by: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    started_at: str | None
    finished_at: str | None
    input_version: str
    result_version: str | None
    last_failure_class: str | None
    completion_token: str | None
    queue_name: str | None
    priority: int | None
    deadline_at: str | None
    created_at: str
    updated_at: str


class ExportSummary(TypedDict):
    export_id: str
    kind: str
    progress_percent: int
    request: JsonObject
    artifact: JsonObject | None


class ExportJob(TypedDict):
    task_id: str
    export_id: str
    kind: str
    status: str
    progress_percent: int
    error: str | None
    request: JsonObject
    artifact: JsonObject | None


class ExportJobPage(TypedDict):
    items: list[ExportJob]
    total: int
    limit: int
    offset: int
    next_offset: int | None


def _export_task_row(row: sqlite3.Row) -> ExportTaskRow:
    return cast(ExportTaskRow, dict(row))


def _parse_export_summary(row: ExportTaskRow, status: str) -> ExportSummary:
    try:
        parsed = json.loads(row["artifacts_summary"] or "")
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    summary = parsed if isinstance(parsed, dict) else {}
    request = summary.get("request") if isinstance(summary.get("request"), dict) else {}
    progress = int(summary.get("progress_percent", 50 if status == "running" else 0))
    if status in {"completed", "failed"}:
        progress = 100
    artifact = summary.get("artifact") if status == "completed" else None
    if isinstance(artifact, dict):
        artifact = dict(artifact)
        artifact.setdefault("download_url", f"/api/exports/{row['task_id']}/download")
    else:
        artifact = None
    return {
        "export_id": str(summary.get("export_id") or row["target_id"]),
        "kind": str(summary.get("kind") or request.get("kind") or "data"),
        "progress_percent": max(0, min(progress, 100)),
        "request": request,
        "artifact": artifact,
    }


def _project(row: ExportTaskRow) -> ExportJob:
    status = {
        "pending": "pending",
        "running": "running",
        "success": "completed",
    }.get(row["status"], "failed")
    summary = _parse_export_summary(row, status)
    return {
        "task_id": row["task_id"],
        "export_id": summary["export_id"],
        "kind": summary["kind"],
        "status": status,
        "progress_percent": summary["progress_percent"],
        "error": redact_text(row["error_reason"]) if status == "failed" and row["error_reason"] else None,
        "request": summary["request"],
        "artifact": summary["artifact"],
    }


def get_export_job(base_dir: Path | str, task_id: str) -> ExportJob | None:
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        row = conn.execute(
            "select * from tasks where task_id = ? and task_type = 'data_export'", (task_id,)
        ).fetchone()
    return _project(_export_task_row(row)) if row is not None else None


def list_export_jobs(
    base_dir: Path | str, *, limit: int, offset: int,
    kind: str | None = None, status: str | None = None,
) -> ExportJobPage:
    bounded_limit = max(1, min(int(limit), 200))
    bounded_offset = max(0, int(offset))
    where = ["task_type = 'data_export'"]
    params: list[str] = []
    if kind:
        where.append("json_extract(artifacts_summary, '$.kind') = ?")
        params.append(kind)
    if status:
        task_statuses = {
            "pending": ["pending"], "running": ["running"], "completed": ["success"],
            "failed": ["failed", "blocked", "skipped"],
        }.get(status)
        if task_statuses is None:
            raise ValueError("invalid export status")
        placeholders = ",".join("?" for _ in task_statuses)
        where.append(f"status in ({placeholders})")
        params.extend(task_statuses)
    where_sql = " and ".join(where)
    with connect_existing(resolve_sqlite_path(base_dir)) as conn:
        total = int(
            conn.execute(f"select count(*) from tasks where {where_sql}", params).fetchone()[0]
        )
        rows = conn.execute(
            f"""select * from tasks where {where_sql}
                 order by created_at desc, task_id desc limit ? offset ?""",
            (*params, bounded_limit, bounded_offset),
        ).fetchall()
    next_offset = bounded_offset + len(rows)
    return {
        "items": [_project(_export_task_row(row)) for row in rows],
        "total": total,
        "limit": bounded_limit,
        "offset": bounded_offset,
        "next_offset": next_offset if next_offset < total else None,
    }


def resolve_export_artifact(base_dir: Path | str, task_id: str) -> tuple[Path, JsonObject]:
    job = get_export_job(base_dir, task_id)
    if job is None:
        raise LookupError("export_not_found")
    if job["status"] == "failed":
        raise RuntimeError("export_failed")
    if job["status"] != "completed" or not job["artifact"]:
        raise RuntimeError("export_not_ready")
    artifact = job["artifact"]
    root = (resolve_data_dir(base_dir) / "exports").resolve()
    candidate = (resolve_data_dir(base_dir) / str(artifact["logical_location"])).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("export_artifact_invalid") from exc
    if not candidate.is_file():
        raise RuntimeError("export_artifact_invalid")
    if candidate.stat().st_size != int(artifact.get("size_bytes", -1)):
        raise RuntimeError("export_artifact_invalid")
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != artifact.get("sha256"):
        raise RuntimeError("export_artifact_invalid")
    return candidate, artifact
