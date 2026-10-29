"""Atomic artifact builders for InfoOS data, backup, and diagnostic exports."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from backend.domain.timestamps import parse_utc

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any
import zipfile

from backend.infrastructure.security.redaction import redact_secrets
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.export_queries import ExportQueries
from backend.infrastructure.storage.paths import resolve_data_dir, resolve_sqlite_path


def build_export_artifact(
    base_dir: Path | str, *, export_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    data_dir = resolve_data_dir(base_dir)
    export_dir = data_dir / "exports" / export_id
    export_dir.mkdir(parents=True, exist_ok=True)
    kind = request["kind"]
    suffix = "json" if kind == "data" and request["format"] == "json" else "zip"
    filename = f"infoos-{kind}.{suffix}"
    final_path = export_dir / filename
    partial = export_dir / f"{filename}.partial"
    partial.unlink(missing_ok=True)
    try:
        if kind == "data":
            included = _write_data(base_dir, partial, request)
        elif kind == "backup":
            included = _write_backup(base_dir, partial, request)
        elif kind == "diagnostic":
            included = _write_diagnostic(base_dir, partial, request)
        else:
            raise ValueError(f"unsupported export kind: {kind}")
        os.replace(partial, final_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    digest = _sha256(final_path)
    created_at = utc_now_micro_z()
    return {
        "file_name": filename,
        "size_bytes": final_path.stat().st_size,
        "sha256": digest,
        "logical_location": str(final_path.relative_to(data_dir)),
        "created_at": created_at,
        "included_sections": included,
        "download_url": "",
    }


def artifact_matches(base_dir: Path | str, artifact: dict[str, Any]) -> bool:
    data_dir = resolve_data_dir(base_dir)
    root = (data_dir / "exports").resolve()
    path = (data_dir / str(artifact.get("logical_location", ""))).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return (
        path.is_file()
        and path.stat().st_size == int(artifact.get("size_bytes", -1))
        and _sha256(path) == artifact.get("sha256")
    )


def _write_data(base_dir: Path | str, target: Path, request: dict[str, Any]) -> list[str]:
    payload = redact_secrets(ExportQueries(base_dir).data_sections(request))
    if request["include_redacted_logs"]:
        payload["logs"] = _redacted_logs(base_dir, request)
    included = [key for key in payload if key != "schema"]
    if request["format"] == "json":
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return included
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _zip_add_bytes(
            bundle, files, "data.json", json.dumps(payload, ensure_ascii=False, indent=2).encode()
        )
        if request["include_attachment_files"]:
            _add_owned_attachments(base_dir, bundle, request=request, files=files)
            included.append("attachment_files")
        _zip_add_manifest(bundle, request, included, files)
    return included


def _write_backup(base_dir: Path | str, target: Path, request: dict[str, Any]) -> list[str]:
    included = ["sqlite_snapshot", "asset_index"]
    queries = ExportQueries(base_dir)
    with tempfile.TemporaryDirectory(prefix="infoos-export-") as temp_dir:
        snapshot = Path(temp_dir) / "infoos.sqlite3"
        _backup_sqlite(resolve_sqlite_path(base_dir), snapshot)
        files: list[dict[str, Any]] = []
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            _zip_add_path(bundle, files, "infoos.sqlite3", snapshot)
            with connect_existing(resolve_sqlite_path(base_dir)) as conn:
                asset_index = queries.asset_index(conn)
            _zip_add_bytes(
                bundle, files, "asset-index.json",
                json.dumps(asset_index, ensure_ascii=False, indent=2).encode(),
            )
            if request["include_media_files"]:
                _add_owned_attachments(base_dir, bundle, request=request, files=files)
                included.append("media_files")
            _zip_add_manifest(bundle, request, included, files)
    return included


def _write_diagnostic(base_dir: Path | str, target: Path, request: dict[str, Any]) -> list[str]:
    from backend.infrastructure.runtime.environment import setup_state_path
    from backend.infrastructure.runtime.setup_state import load_state
    from backend.infrastructure.storage.source_run_trace_queries import SourceRunTraceQueries

    included = ["redacted_logs"]
    diagnostic: dict[str, Any] = {
        "logs": _redacted_logs(base_dir, request),
        "version_info": {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "export_schema": "infoos.export.v1",
        },
    }
    included.append("version_info")
    if request["include_environment_snapshot"]:
        state = load_state(setup_state_path())
        persisted = state.to_dict()
        diagnostic["environment_snapshot"] = redact_secrets(
            {
                "component_snapshot_at": persisted["component_snapshot_at"],
                "components": persisted["components"],
                "accounts": {
                    platform: {"status": entry["status"], "last_checked": entry["last_checked"]}
                    for platform, entry in persisted["accounts"].items()
                },
            }
        )
        included.append("environment_snapshot")
    if request["source_run_ids"]:
        queries = SourceRunTraceQueries(base_dir)
        export_queries = ExportQueries(base_dir)
        source_runs = []
        for public_id in request["source_run_ids"]:
            task_id = export_queries.resolve_public_source_run_id(public_id)
            if task_id is None:
                continue
            head = queries.load_trace_head(task_id)
            if head is None:
                continue
            pipeline = queries.load_list_pipeline_rows([head])
            source_runs.append(_source_run_summary(head, pipeline))
        diagnostic["source_runs"] = redact_secrets(source_runs)
        included.append("source_run_summaries")
    files: list[dict[str, Any]] = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        _zip_add_bytes(
            bundle, files, "diagnostic.json",
            json.dumps(diagnostic, ensure_ascii=False, indent=2).encode(),
        )
        _zip_add_manifest(bundle, request, included, files)
    return included


def _add_owned_attachments(
    base_dir: Path | str, bundle: zipfile.ZipFile, *, request: dict[str, Any],
    files: list[dict[str, Any]],
) -> None:
    assets_root = (resolve_data_dir(base_dir) / "assets").resolve()
    for row in ExportQueries(base_dir).attachment_rows(request):
        candidate = (assets_root / str(row["local_path"])).resolve()
        try:
            candidate.relative_to(assets_root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        expected_size = row.get("size_bytes")
        if expected_size is not None and candidate.stat().st_size != int(expected_size):
            continue
        expected_hash = row.get("content_hash")
        if expected_hash and _sha256(candidate) != expected_hash:
            continue
        safe_name = Path(candidate.name).name
        _zip_add_path(bundle, files, f"attachments/{row['asset_id']}/{safe_name}", candidate)


def _redacted_logs(base_dir: Path | str, request: dict[str, Any]) -> list[dict[str, Any]]:
    from backend.infrastructure.observability.log_records import read_log_records
    from backend.infrastructure.observability.logging import resolve_log_path
    from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository

    records = read_log_records(resolve_log_path(base_dir), max_records=2_000)
    try:
        audits = ApiRequestLogRepository(base_dir).list(
            created_from=request.get("time_from"), created_to=request.get("time_to"),
            limit=2_000, offset=0,
        )["items"]
    except (FileNotFoundError, sqlite3.Error):
        audits = []
    records.extend(
        {
            "timestamp": item["created_at"],
            "level": "ERROR" if item["status_code"] >= 500 else (
                "WARNING" if item["status_code"] >= 400 else "INFO"
            ),
            "area": "interface",
            "message": " ".join(
                str(value) for value in (
                    item.get("method"), item.get("path"), item.get("status_code"),
                    item.get("result_code"), item.get("result_summary"),
                ) if value not in (None, "")
            ),
            "request_id": item.get("request_id"),
        }
        for item in audits
    )
    levels = set(request["log_levels"])
    filtered = [record for record in records if not levels or record.get("level") in levels]
    if request.get("time_from"):
        threshold = parse_utc(str(request["time_from"]))
        filtered = [record for record in filtered if parse_utc(str(record.get("timestamp"))) >= threshold]
    if request.get("time_to"):
        threshold = parse_utc(str(request["time_to"]))
        filtered = [record for record in filtered if parse_utc(str(record.get("timestamp"))) <= threshold]
    filtered.sort(key=lambda record: str(record.get("timestamp") or ""), reverse=True)
    return redact_secrets(filtered)




def _source_run_summary(head: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any]:
    checkpoint = head.get("checkpoint") if isinstance(head.get("checkpoint"), dict) else {}
    return {
        "task_id": head.get("task_id"),
        "source_id": head.get("target_id"),
        "platform": head.get("platform"),
        "source_type": head.get("source_type"),
        "display_name": head.get("display_name"),
        "status": head.get("status"),
        "attempt": head.get("attempt"),
        "max_attempts": head.get("max_attempts"),
        "started_at": head.get("started_at"),
        "finished_at": head.get("finished_at"),
        "failure_class": head.get("last_failure_class"),
        "run_id": head.get("run_id"),
        "fetched_count": head.get("fetched_count"),
        "visible_count": head.get("visible_count"),
        "new_count": head.get("new_count"),
        "dedup_skipped_count": head.get("dedup_skipped_count"),
        "checkpoint_counts": {
            "raw_items": len(checkpoint.get("raw_item_ids") or checkpoint.get("raw_item_keys") or []),
            "card_build_tasks": len(checkpoint.get("card_build_task_ids") or []),
        },
        "pipeline_tasks": [
            {
                "task_id": row.get("task_id"),
                "task_type": row.get("task_type"),
                "target_kind": row.get("target_kind"),
                "target_id": row.get("target_id"),
                "status": row.get("status"),
                "attempt": row.get("attempt"),
                "last_failure_class": row.get("last_failure_class"),
            }
            for row in pipeline.get("graph", [])
        ],
        "trace_truncated": bool(pipeline.get("page_truncated")),
    }


def _backup_sqlite(source: Path, target: Path) -> None:
    with connect_existing(source) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
    with sqlite3.connect(target) as verify:
        result = verify.execute("pragma integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError("backup_integrity_failed")


def _zip_add_manifest(
    bundle: zipfile.ZipFile,
    request: dict[str, Any],
    sections: list[str],
    files: list[dict[str, Any]],
) -> None:
    manifest = {
        "schema": "infoos.export.manifest.v1",
        "generated_at": utc_now_micro_z(),
        "request": redact_secrets(request),
        "included_sections": list(sections),
        "files": files,
        "manifest_file": "manifest.json",
        "scope_notes": [
            "sources and source_config_versions are configuration snapshots",
            "manifest.json is excluded from its own file hash list",
        ],
    }
    bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))


def _zip_add_bytes(
    bundle: zipfile.ZipFile, files: list[dict[str, Any]], path: str, content: bytes
) -> None:
    bundle.writestr(path, content)
    files.append(
        {"path": path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    )


def _zip_add_path(
    bundle: zipfile.ZipFile, files: list[dict[str, Any]], path: str, source: Path
) -> None:
    bundle.write(source, path)
    files.append(
        {"path": path, "size_bytes": source.stat().st_size, "sha256": _sha256(source)}
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
