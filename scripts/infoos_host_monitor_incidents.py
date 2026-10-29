"""Bounded incident-bundle storage for the InfoOS Linux host monitor."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from infoos_host_monitor_lib import (
    MonitorPaths,
    durable_write_text,
    fsync_directory,
)

DEFAULT_MAX_INCIDENTS = 12
DEFAULT_MAX_INCIDENT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_TOTAL_INCIDENT_BYTES = 512 * 1024 * 1024
DEFAULT_INCIDENT_KEEP_FREE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
STALE_PENDING_SECONDS = 2 * 60 * 60


def _positive_environment_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if parsed <= 0:
        raise RuntimeError(f"{name} must be greater than zero")
    return parsed


def incident_limits() -> dict[str, int]:
    return {
        "max_incidents": _positive_environment_int(
            "INFOOS_HOST_MONITOR_MAX_INCIDENTS", DEFAULT_MAX_INCIDENTS
        ),
        "max_incident_bytes": _positive_environment_int(
            "INFOOS_HOST_MONITOR_MAX_INCIDENT_BYTES", DEFAULT_MAX_INCIDENT_BYTES
        ),
        "max_total_bytes": _positive_environment_int(
            "INFOOS_HOST_MONITOR_MAX_TOTAL_INCIDENT_BYTES",
            DEFAULT_MAX_TOTAL_INCIDENT_BYTES,
        ),
        "keep_free_bytes": _positive_environment_int(
            "INFOOS_HOST_MONITOR_KEEP_FREE_BYTES",
            DEFAULT_INCIDENT_KEEP_FREE_BYTES,
        ),
        "max_artifact_bytes": _positive_environment_int(
            "INFOOS_HOST_MONITOR_MAX_ARTIFACT_BYTES",
            DEFAULT_MAX_ARTIFACT_BYTES,
        ),
    }


def directory_bytes(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def _incident_directories(paths: MonitorPaths) -> list[Path]:
    incidents_root = paths.incidents_dir.resolve()
    incidents: list[Path] = []
    for candidate in paths.incidents_dir.iterdir():
        if candidate.name.startswith(".pending-"):
            continue
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            if candidate.resolve().parent != incidents_root:
                continue
            candidate.stat()
        except OSError:
            continue
        incidents.append(candidate)
    return sorted(incidents, key=lambda item: (item.stat().st_mtime_ns, item.name))


def _remove_incident(path: Path, incidents_root: Path) -> None:
    if path.is_symlink() or path.resolve().parent != incidents_root.resolve():
        raise RuntimeError(f"refusing to remove unsafe incident path: {path}")
    root_fd = os.open(incidents_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        shutil.rmtree(path.name, dir_fd=root_fd)
    finally:
        os.close(root_fd)
    fsync_directory(incidents_root)


def _remove_stale_pending_directories(paths: MonitorPaths) -> None:
    for candidate in paths.incidents_dir.glob(".pending-*"):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            if time.time() - candidate.stat().st_mtime < STALE_PENDING_SECONDS:
                continue
        except OSError:
            continue
        _remove_incident(candidate, paths.incidents_dir)


def prune_incidents(paths: MonitorPaths, *, reserve_bytes: int = 0) -> None:
    _remove_stale_pending_directories(paths)
    limits = incident_limits()
    incidents = _incident_directories(paths)
    total_bytes = sum(directory_bytes(path) for path in incidents)
    count_limit = limits["max_incidents"] - (1 if reserve_bytes else 0)
    while incidents and (
        len(incidents) > count_limit
        or total_bytes + reserve_bytes > limits["max_total_bytes"]
        or shutil.disk_usage(paths.incidents_dir).free
        < limits["keep_free_bytes"] + reserve_bytes
    ):
        oldest = incidents.pop(0)
        oldest_bytes = directory_bytes(oldest)
        _remove_incident(oldest, paths.incidents_dir)
        total_bytes = max(0, total_bytes - oldest_bytes)
    if total_bytes + reserve_bytes > limits["max_total_bytes"]:
        raise RuntimeError("incident retention byte budget is too small")
    if shutil.disk_usage(paths.incidents_dir).free < limits["keep_free_bytes"] + reserve_bytes:
        raise RuntimeError("incident collection would violate the disk keep-free reserve")


def _bounded_text(value: str, limit: int) -> str:
    payload = value.encode("utf-8", errors="replace")
    if len(payload) <= limit:
        return value
    marker = b"\n[truncated by infoos-host-monitor]\n"
    return (payload[: max(0, limit - len(marker))] + marker).decode("utf-8", errors="ignore")


def write_bundle_text(
    incident_dir: Path,
    relative_path: str,
    value: str,
    remaining_bytes: int,
    artifact_limit: int,
) -> int:
    allowance = min(remaining_bytes, artifact_limit)
    if allowance <= 0:
        return 0
    bounded = _bounded_text(value, allowance)
    durable_write_text(incident_dir / relative_path, bounded)
    return (incident_dir / relative_path).stat().st_size


def copy_bundle_file(
    source: Path,
    destination: Path,
    remaining_bytes: int,
    artifact_limit: int,
) -> int:
    allowance = min(remaining_bytes, artifact_limit)
    try:
        source_size = source.stat().st_size
    except OSError:
        return 0
    if allowance <= 0 or source_size > allowance or source.is_symlink():
        return 0
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as input_handle, temporary.open("xb") as output_handle:
            copied = 0
            while copied <= allowance:
                chunk = input_handle.read(min(1024 * 1024, allowance + 1 - copied))
                if not chunk:
                    break
                output_handle.write(chunk)
                copied += len(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if copied > allowance:
            return 0
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        fsync_directory(destination.parent)
        return copied
    except OSError:
        return 0
    finally:
        temporary.unlink(missing_ok=True)
