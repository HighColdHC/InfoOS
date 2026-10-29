"""Bounded parsing of current and rotated InfoOS log files."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, TypedDict, cast

from backend.domain.json_types import JsonObject
from backend.infrastructure.security.redaction import redact_secrets, redact_text


class LogRecordFields(TypedDict):
    timestamp: str
    level: str
    area: str
    message: str
    object_type: str | None
    object_id: str | None
    request_id: str | None
    source_id: str | None
    task_id: str | None
    worker_id: str | None


class LogRecord(LogRecordFields):
    log_id: str


LOG_AREAS = frozenset({"card", "fetch", "media", "asr", "browser", "interface", "storage", "system"})
LOG_FIELDS = (
    "log_id",
    "timestamp",
    "level",
    "area",
    "message",
    "object_type",
    "object_id",
    "request_id",
    "source_id",
    "task_id",
    "worker_id",
)
DEFAULT_MAX_RECORDS = 5_000
DEFAULT_MAX_BYTES = 12_000_000
ROTATION_COUNT = 5

_LEGACY_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?)\s+"
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(?P<logger>\S+)\s+(?P<message>.*)$"
)


def read_log_records(
    log_path: str | Path,
    *,
    max_records: int = DEFAULT_MAX_RECORDS,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> list[LogRecord]:
    """Read newest records from current + five rotations within hard bounds."""
    record_cap = max(0, int(max_records))
    byte_budget = max(0, int(max_bytes))
    if record_cap == 0 or byte_budget == 0:
        return []
    records: list[LogRecord] = []
    root = Path(log_path)
    for path in _log_paths(root):
        if len(records) >= record_cap or byte_budget <= 0 or not path.is_file():
            continue
        lines, consumed = _bounded_reverse_lines(path, byte_budget)
        byte_budget -= consumed
        fallback_timestamp = _file_timestamp(path)
        for raw_line in lines:
            if len(records) >= record_cap:
                break
            record = parse_log_line(raw_line, fallback_timestamp=fallback_timestamp)
            if record is not None:
                records.append(record)
    records.sort(key=lambda item: (item["timestamp"], item["log_id"]), reverse=True)
    return records


def parse_log_line(line: str, *, fallback_timestamp: str) -> LogRecord | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        payload: object = json.loads(stripped)
    except json.JSONDecodeError:
        payload = _parse_legacy_line(stripped, fallback_timestamp=fallback_timestamp)
    if not isinstance(payload, dict):
        payload = {"timestamp": fallback_timestamp, "level": "INFO", "area": "system", "message": stripped}
    normalized = _normalize_record(payload, fallback_timestamp=fallback_timestamp)
    record: LogRecord = {**normalized, "log_id": _log_id(normalized)}
    return cast(LogRecord, redact_secrets(record))


def stable_log_id(payload: Mapping[str, Any], *, namespace: str = "file") -> str:
    canonical = json.dumps(
        {key: payload.get(key) for key in LOG_FIELDS if key != "log_id"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"{namespace}:{canonical}".encode("utf-8")).hexdigest()[:24]


def infer_area(*values: str | None) -> str:
    text = " ".join(str(value or "").lower() for value in values)
    markers = (
        ("interface", ("interface", "plugin", "api_request")),
        ("browser", ("browser", "opencli", "chrome")),
        ("storage", ("storage", "sqlite", "database")),
        ("asr", ("asr", "transcrib", "transcript")),
        ("media", ("media", "video", "image", "asset")),
        ("fetch", ("fetch", "source", "connector", "acquisition")),
        ("card", ("card", "content_block")),
    )
    for area, hints in markers:
        if any(hint in text for hint in hints):
            return area
    return "system"


def _log_paths(root: Path) -> tuple[Path, ...]:
    return (root, *(Path(f"{root}.{index}") for index in range(1, ROTATION_COUNT + 1)))


def _bounded_reverse_lines(path: Path, byte_budget: int) -> tuple[list[str], int]:
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    read_size = min(size, max(0, int(byte_budget)))
    if read_size <= 0:
        return [], 0
    try:
        with path.open("rb") as handle:
            start = size - read_size
            handle.seek(start)
            data = handle.read(read_size)
    except OSError:
        return [], 0
    parts = data.splitlines()
    if start > 0 and parts:
        parts = parts[1:]
    return [part.decode("utf-8", errors="replace") for part in reversed(parts)], len(data)


def _parse_legacy_line(line: str, *, fallback_timestamp: str) -> JsonObject:
    match = _LEGACY_PATTERN.match(line)
    if match is None:
        return {
            "timestamp": fallback_timestamp,
            "level": "INFO",
            "area": "system",
            "message": line,
        }
    values = match.groupdict()
    return {
        "timestamp": _normalize_timestamp(values["timestamp"], fallback_timestamp),
        "level": values["level"],
        "area": infer_area(values["logger"], values["message"]),
        "message": values["message"],
    }


def _normalize_record(payload: JsonObject, *, fallback_timestamp: str) -> LogRecordFields:
    area = str(payload.get("area") or "").strip().lower()
    if area not in LOG_AREAS:
        area = infer_area(str(payload.get("logger") or ""), str(payload.get("message") or ""))
    level = str(payload.get("level") or "INFO").strip().upper()
    if level == "WARN":
        level = "WARNING"
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level = "INFO"
    return {
        "timestamp": _normalize_timestamp(str(payload.get("timestamp") or ""), fallback_timestamp),
        "level": level,
        "area": area,
        "message": redact_text(str(payload.get("message") or ""))[:8_000],
        "object_type": _optional_text(payload.get("object_type")),
        "object_id": _optional_text(payload.get("object_id")),
        "request_id": _optional_text(payload.get("request_id")),
        "source_id": _optional_text(payload.get("source_id")),
        "task_id": _optional_text(payload.get("task_id")),
        "worker_id": _optional_text(payload.get("worker_id")),
    }


def _normalize_timestamp(value: str, fallback: str) -> str:
    normalized = value.strip()
    if not normalized:
        return fallback
    try:
        parsed = datetime.fromisoformat(normalized.replace(",", ".").replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _file_timestamp(path: Path) -> str:
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0
    return datetime.fromtimestamp(modified, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return redact_text(str(value).strip())[:512] or None


def _log_id(payload: Mapping[str, Any]) -> str:
    return stable_log_id(payload, namespace="file")
