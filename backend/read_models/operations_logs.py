"""Read-only union of system log files and plugin Interface request audits."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

from backend.infrastructure.observability.log_records import (
    LogRecord,
    LogRecordFields,
    LOG_AREAS,
    read_log_records,
    stable_log_id,
)
from backend.infrastructure.observability.logging import resolve_log_path
from backend.infrastructure.security.redaction import redact_secrets, redact_text
from backend.infrastructure.storage.api_request_logs import ApiRequestLogRepository, ApiRequestLogRow


class LogSummary(TypedDict):
    total: int
    info: int
    warning: int
    error: int


class OperationsLogPage(TypedDict):
    summary: LogSummary
    items: list[LogRecord]
    total: int
    limit: int
    offset: int
    next_offset: int | None


LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
MAX_AUDIT_RECORDS = 5_000


def read_operations_log_page(
    base_dir: Path | str,
    *,
    query: str | None = None,
    level: str | None = None,
    area: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    object_id: str | None = None,
    request_id: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> OperationsLogPage:
    records = _all_records(base_dir)
    filtered = [
        record
        for record in records
        if _matches(
            record,
            query=query,
            level=level,
            area=area,
            time_from=time_from,
            time_to=time_to,
            object_id=object_id,
            request_id=request_id,
        )
    ]
    filtered.sort(key=lambda item: (item["timestamp"], item["log_id"]), reverse=True)
    bounded_limit = max(1, min(int(limit), 100))
    bounded_offset = max(0, int(offset))
    items = filtered[bounded_offset : bounded_offset + bounded_limit]
    consumed = bounded_offset + len(items)
    payload: OperationsLogPage = {
        "summary": _summary(filtered),
        "items": items,
        "total": len(filtered),
        "limit": bounded_limit,
        "offset": bounded_offset,
        "next_offset": consumed if consumed < len(filtered) else None,
    }
    return cast(OperationsLogPage, redact_secrets(payload))


def read_operations_log_detail(base_dir: Path | str, log_id: str) -> LogRecord | None:
    record = next((item for item in _all_records(base_dir) if item["log_id"] == log_id), None)
    return cast(LogRecord, redact_secrets(record)) if record is not None else None


def valid_log_level(value: str) -> bool:
    return value.strip().upper() in LOG_LEVELS


def valid_log_area(value: str) -> bool:
    return value.strip().lower() in LOG_AREAS


def _all_records(base_dir: Path | str) -> list[LogRecord]:
    records = read_log_records(resolve_log_path(base_dir))
    records.extend(_interface_records(base_dir))
    return records


def _interface_records(base_dir: Path | str) -> list[LogRecord]:
    try:
        page = ApiRequestLogRepository(base_dir).list(limit=MAX_AUDIT_RECORDS, offset=0)
    except (FileNotFoundError, sqlite3.Error):
        return []
    return [_interface_record(item) for item in page["items"]]


def _interface_record(item: ApiRequestLogRow) -> LogRecord:
    status_code = item["status_code"]
    level = "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO"
    request_id = item["request_id"]
    message = redact_text(
        " ".join(
            part
            for part in (
                item["method"], item["path"], str(status_code), item["result_code"], item["result_summary"],
            )
            if part
        )
    )
    fields: LogRecordFields = {
        "timestamp": _normalized_timestamp(item["created_at"]),
        "level": level,
        "area": "interface",
        "message": message,
        "object_type": "request",
        "object_id": request_id,
        "request_id": request_id,
        "source_id": None,
        "task_id": None,
        "worker_id": None,
    }
    record: LogRecord = {**fields, "log_id": stable_log_id(fields, namespace="interface")}
    return cast(LogRecord, redact_secrets(record))


def _matches(
    record: LogRecord,
    *,
    query: str | None,
    level: str | None,
    area: str | None,
    time_from: str | None,
    time_to: str | None,
    object_id: str | None,
    request_id: str | None,
) -> bool:
    if level and record["level"] != level.strip().upper():
        return False
    if area and record["area"] != area.strip().lower():
        return False
    record_time = _timestamp_value(record["timestamp"])
    if time_from and record_time < _timestamp_value(time_from):
        return False
    if time_to and record_time > _timestamp_value(time_to):
        return False
    if object_id and (record["object_id"] or "") != object_id:
        return False
    if request_id and (record["request_id"] or "") != request_id:
        return False
    normalized_query = (query or "").strip().casefold()
    if normalized_query:
        values = (
            record["log_id"], record["timestamp"], record["level"], record["area"], record["message"],
            record["object_type"], record["object_id"], record["request_id"],
            record["source_id"], record["task_id"], record["worker_id"],
        )
        haystack = " ".join(value or "" for value in values).casefold()
        if normalized_query not in haystack:
            return False
    return True


def _summary(records: list[LogRecord]) -> LogSummary:
    return {
        "total": len(records),
        "info": sum(item["level"] in {"DEBUG", "INFO"} for item in records),
        "warning": sum(item["level"] == "WARNING" for item in records),
        "error": sum(item["level"] in {"ERROR", "CRITICAL"} for item in records),
    }


def _timestamp_value(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_timestamp(value: str) -> str:
    try:
        parsed = _timestamp_value(value)
    except ValueError:
        parsed = datetime.fromtimestamp(0, timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
