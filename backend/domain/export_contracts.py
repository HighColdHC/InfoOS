"""Pure contracts for task-backed InfoOS exports."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Any, Mapping


EXPORT_FIELDS = (
    "kind",
    "format",
    "time_from",
    "time_to",
    "include_sources_runs",
    "include_cards_content",
    "include_asset_index",
    "include_attachment_files",
    "include_redacted_logs",
    "include_media_files",
    "log_levels",
    "include_environment_snapshot",
    "source_run_ids",
)
EXPORT_KINDS = frozenset({"data", "backup", "diagnostic"})
EXPORT_FORMATS = frozenset({"json", "zip"})
LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ExportRequestError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _iso(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ExportRequestError("invalid_export_time", f"{field} must be an ISO timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExportRequestError("invalid_export_time", f"{field} must be an ISO timestamp") from exc
    return value


def _unique_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ExportRequestError("invalid_export_request", "list fields must contain strings")
    return list(dict.fromkeys(item for item in value if item))


def normalize_export_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and reduce a request to the frozen public contract."""
    kind = str(payload.get("kind", ""))
    if kind not in EXPORT_KINDS:
        raise ExportRequestError("invalid_export_kind", "kind must be data, backup, or diagnostic")
    output_format = str(payload.get("format") or ("json" if kind == "data" else "zip"))
    if output_format not in EXPORT_FORMATS:
        raise ExportRequestError("invalid_export_format", "format must be json or zip")

    time_from = _iso(payload.get("time_from"), "time_from")
    time_to = _iso(payload.get("time_to"), "time_to")
    if time_from and time_to:
        left = datetime.fromisoformat(time_from.replace("Z", "+00:00"))
        right = datetime.fromisoformat(time_to.replace("Z", "+00:00"))
        try:
            invalid_range = left > right
        except TypeError as exc:
            raise ExportRequestError(
                "invalid_export_time_range", "time_from and time_to must use compatible offsets"
            ) from exc
        if invalid_range:
            raise ExportRequestError(
                "invalid_export_time_range", "time_from cannot be after time_to"
            )

    request = {
        "kind": kind,
        "format": output_format,
        "time_from": time_from,
        "time_to": time_to,
        "include_sources_runs": bool(payload.get("include_sources_runs", False)),
        "include_cards_content": bool(payload.get("include_cards_content", False)),
        "include_asset_index": bool(payload.get("include_asset_index", True)),
        "include_attachment_files": bool(payload.get("include_attachment_files", False)),
        "include_redacted_logs": bool(payload.get("include_redacted_logs", False)),
        "include_media_files": bool(payload.get("include_media_files", False)),
        "log_levels": _unique_strings(payload.get("log_levels")),
        "include_environment_snapshot": bool(payload.get("include_environment_snapshot", False)),
        "source_run_ids": _unique_strings(payload.get("source_run_ids")),
    }

    if kind == "data":
        if request["include_attachment_files"]:
            request["format"] = "zip"
            request["include_asset_index"] = True
        request["include_media_files"] = False
        request["include_environment_snapshot"] = False
        request["source_run_ids"] = []
        request["log_levels"] = []
        if not any(
            request[field]
            for field in ("include_sources_runs", "include_cards_content", "include_redacted_logs")
        ):
            raise ExportRequestError("empty_data_export", "select at least one data section")
    elif kind == "backup":
        request.update(
            format="zip",
            time_from=None,
            time_to=None,
            include_sources_runs=False,
            include_cards_content=False,
            include_asset_index=True,
            include_attachment_files=False,
            include_redacted_logs=False,
            log_levels=[],
            include_environment_snapshot=False,
            source_run_ids=[],
        )
    else:
        levels = []
        for level in request["log_levels"]:
            normalized = "WARNING" if level.upper() == "WARN" else level.upper()
            if normalized not in LOG_LEVELS:
                raise ExportRequestError("invalid_log_level", f"unsupported log level: {level}")
            if normalized not in levels:
                levels.append(normalized)
        request.update(
            format="zip",
            include_sources_runs=False,
            include_cards_content=False,
            include_asset_index=False,
            include_attachment_files=False,
            include_redacted_logs=True,
            include_media_files=False,
            log_levels=levels,
        )
    return {field: request[field] for field in EXPORT_FIELDS}


def export_request_input_version(request: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(request), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"export:v1:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"
