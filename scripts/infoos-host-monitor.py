#!/usr/bin/env python3
"""Durable host heartbeats and boot-forensics bundles for the InfoOS Linux host."""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import signal
import time
from pathlib import Path
from typing import Any

from infoos_host_monitor_lib import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_LOG_GENERATIONS,
    DEFAULT_MAX_LOG_BYTES,
    MonitorPaths,
    append_jsonl,
    assess_memory_pressure as assess_memory_pressure,
    classify_boot,
    collect_snapshot,
    collect_top_process_memory as collect_top_process_memory,
    current_boot_id,
    detect_anomalies as detect_anomalies,
    durable_write_json,
    durable_write_text,
    ensure_directories,
    fsync_directory,
    incident_name,
    iso_now,
    parse_meminfo as parse_meminfo,
    parse_proc_stat as parse_proc_stat,
    paths_from_environment,
    read_json,
    rotate_log,
    run_command,
    tail_lines,
)
from infoos_host_monitor_incidents import (
    copy_bundle_file,
    incident_limits,
    prune_incidents,
    write_bundle_text,
)

MEMORY_TERMINATION_KINDS = {
    "cgroup_oom",
    "cgroup_oom_kill",
    "container_oom_killed",
    "container_sigkill_exit",
}


def collect_incident(
    paths: MonitorPaths,
    reason: str,
    classification: dict[str, Any] | None = None,
    include_previous_boot: bool = True,
) -> Path:
    ensure_directories(paths)
    limits = incident_limits()
    prune_incidents(paths, reserve_bytes=limits["max_incident_bytes"])
    final_incident_dir = paths.incidents_dir / incident_name(reason)
    incident_dir = paths.incidents_dir / f".pending-{final_incident_dir.name}-{os.getpid()}"
    incident_dir.mkdir(parents=False, mode=0o700)
    used_bytes = 0

    heartbeat_paths = [
        paths.heartbeat_log.with_name(f"{paths.heartbeat_log.name}.{generation}")
        for generation in range(DEFAULT_LOG_GENERATIONS, 0, -1)
    ] + [paths.heartbeat_log]
    used_bytes += write_bundle_text(
        incident_dir,
        "heartbeat-tail.jsonl",
        "".join(tail_lines(heartbeat_paths, 1200)),
        limits["max_incident_bytes"] - used_bytes,
        limits["max_artifact_bytes"],
    )

    commands: dict[str, list[str]] = {
        "boot-history.txt": ["last", "-x", "-F"],
        "current-kernel-journal.txt": [
            "journalctl",
            "-b",
            "0",
            "-k",
            "--no-pager",
            "-o",
            "short-iso-precise",
        ],
        "ras-summary.txt": ["ras-mc-ctl", "--summary"],
        "ras-service-journal.txt": [
            "journalctl",
            "-b",
            "0",
            "-u",
            "rasdaemon.service",
            "--no-pager",
            "-o",
            "short-iso-precise",
        ],
        "dmesg.txt": ["dmesg", "-T"],
        "memory-dmi.txt": ["dmidecode", "-t", "memory"],
        "pci.txt": ["lspci", "-vv"],
        "failed-units.txt": ["systemctl", "--failed", "--no-pager"],
        "watchdog.txt": ["wdctl"],
        "power-policy.txt": ["tlp-stat", "-s", "-p"],
    }
    if include_previous_boot:
        commands["previous-boot-journal.txt"] = [
            "journalctl",
            "-b",
            "-1",
            "--no-pager",
            "-o",
            "short-iso-precise",
        ]
    for filename, command in commands.items():
        used_bytes += write_bundle_text(
            incident_dir,
            filename,
            run_command(command, timeout=90),
            limits["max_incident_bytes"] - used_bytes,
            limits["max_artifact_bytes"],
        )

    pstore_dir = incident_dir / "pstore"
    pstore_dir.mkdir(mode=0o700)
    for source_name in glob.glob("/sys/fs/pstore/*"):
        used_bytes += copy_bundle_file(
            Path(source_name),
            pstore_dir / Path(source_name).name,
            limits["max_incident_bytes"] - used_bytes,
            limits["max_artifact_bytes"],
        )

    sysstat_dir = incident_dir / "sysstat"
    sysstat_dir.mkdir(mode=0o700)
    for source_name in sorted(glob.glob("/var/log/sysstat/sa*"))[-4:]:
        used_bytes += copy_bundle_file(
            Path(source_name),
            sysstat_dir / Path(source_name).name,
            limits["max_incident_bytes"] - used_bytes,
            limits["max_artifact_bytes"],
        )

    used_bytes += copy_bundle_file(
        Path("/var/lib/rasdaemon/ras-mc_event.db"),
        incident_dir / "ras-mc_event.db",
        limits["max_incident_bytes"] - used_bytes,
        limits["max_artifact_bytes"],
    )
    metadata = {
        "schema_version": 1,
        "created_at": iso_now(),
        "reason": reason,
        "classification": classification,
        "current_boot_id": current_boot_id(),
        "path": str(final_incident_dir),
        "bundle_limits": limits,
        "artifact_bytes_before_metadata": used_bytes,
    }
    metadata_payload = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    metadata_bytes = len(metadata_payload.encode("utf-8"))
    if used_bytes + metadata_bytes > limits["max_incident_bytes"]:
        shutil.rmtree(incident_dir)
        raise RuntimeError("incident metadata exceeds the remaining bundle byte budget")
    durable_write_text(incident_dir / "metadata.json", metadata_payload)
    fsync_directory(incident_dir)
    os.replace(incident_dir, final_incident_dir)
    fsync_directory(paths.incidents_dir)
    prune_incidents(paths)
    return final_incident_dir


def boot_check(paths: MonitorPaths) -> int:
    ensure_directories(paths)
    new_boot_id = current_boot_id()
    previous_state = read_json(paths.boot_state)
    clean_marker = read_json(paths.clean_marker)
    classification = classify_boot(previous_state, clean_marker, new_boot_id)
    classification["classified_at"] = iso_now()

    if classification["status"] == "abnormal_termination":
        incident_dir = collect_incident(
            paths,
            "abnormal-termination",
            classification=classification,
            include_previous_boot=True,
        )
        classification["incident_path"] = str(incident_dir)

    durable_write_json(paths.last_classification, classification)
    durable_write_json(paths.boots_dir / f"{new_boot_id}.json", classification)
    durable_write_json(
        paths.boot_state,
        {
            "boot_id": new_boot_id,
            "observed_at": iso_now(),
            "classification": classification["status"],
        },
    )
    if clean_marker and clean_marker.get("boot_id") != new_boot_id:
        paths.clean_marker.unlink(missing_ok=True)
        fsync_directory(paths.clean_marker.parent)
    append_jsonl(paths.notes_log, {"event": "boot_check", **classification})
    print(json.dumps(classification, ensure_ascii=False, indent=2))
    return 0


def mark_clean_shutdown(paths: MonitorPaths, only_if_stopping: bool) -> int:
    ensure_directories(paths)
    system_state = run_command(["systemctl", "is-system-running"], timeout=5)
    if only_if_stopping and "stopping" not in system_state:
        append_jsonl(
            paths.notes_log,
            {
                "event": "clean_marker_skipped",
                "timestamp": iso_now(),
                "system_state": system_state.strip(),
            },
        )
        return 0
    marker = {
        "event": "clean_shutdown",
        "timestamp": iso_now(),
        "boot_id": current_boot_id(),
        "system_state": system_state.strip(),
    }
    durable_write_json(paths.clean_marker, marker)
    append_jsonl(paths.notes_log, marker)
    return 0


def update_live_anomaly_episode(
    paths: MonitorPaths,
    snapshot: dict[str, Any],
) -> Path | None:
    anomalies = snapshot.get("anomalies", [])
    state_path = paths.state_dir / "last-live-incident.json"
    previous = read_json(state_path) or {}
    if not anomalies:
        if previous.get("active") is True:
            durable_write_json(
                state_path,
                {
                    **previous,
                    "active": False,
                    "recovered_at": snapshot["timestamp"],
                    "recovered_boot_id": snapshot["boot_id"],
                },
            )
            append_jsonl(
                paths.notes_log,
                {
                    "event": "live_anomaly_recovered",
                    "timestamp": snapshot["timestamp"],
                    "boot_id": snapshot["boot_id"],
                    "previous_anomaly_kinds": previous.get("anomaly_kinds", []),
                },
            )
        return None
    if previous.get("active") is True:
        return None

    incident_dir = collect_incident(
        paths,
        f"live-anomaly-{anomalies[0]['kind']}",
        classification={"status": "live_anomaly", "anomalies": anomalies},
        include_previous_boot=False,
    )
    state = {
        "active": True,
        "started_at": snapshot["timestamp"],
        "boot_id": snapshot["boot_id"],
        "anomaly_kinds": sorted(
            {str(anomaly.get("kind")) for anomaly in anomalies if anomaly.get("kind")}
        ),
        "incident_path": str(incident_dir),
    }
    durable_write_json(state_path, state)
    append_jsonl(
        paths.notes_log,
        {
            "event": "live_anomaly_incident",
            "timestamp": snapshot["timestamp"],
            "anomalies": anomalies,
            "incident_path": str(incident_dir),
        },
    )
    return incident_dir


def monitor_loop(
    paths: MonitorPaths,
    interval_seconds: int,
    max_log_bytes: int,
    generations: int,
) -> int:
    ensure_directories(paths)
    stopping = False
    previous_snapshot: dict[str, Any] | None = None
    previous_lines = tail_lines([paths.heartbeat_log], 1)
    if previous_lines:
        try:
            candidate = json.loads(previous_lines[0])
            previous_snapshot = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            pass

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    next_run = time.monotonic()
    while not stopping:
        try:
            rotate_log(paths.heartbeat_log, max_log_bytes, generations)
            snapshot = collect_snapshot(paths, previous_snapshot=previous_snapshot)
            append_jsonl(paths.heartbeat_log, snapshot)
            previous_snapshot = snapshot
            anomalies = snapshot.get("anomalies", [])
            memory_termination_signals = [
                anomaly
                for anomaly in anomalies
                if anomaly.get("kind") in MEMORY_TERMINATION_KINDS
            ]
            if memory_termination_signals:
                confirmed = any(
                    signal.get("kind") in {"cgroup_oom_kill", "container_oom_killed"}
                    for signal in memory_termination_signals
                )
                durable_write_json(
                    paths.last_memory_termination,
                    {
                        "status": "confirmed" if confirmed else "suspected",
                        "timestamp": snapshot["timestamp"],
                        "boot_id": snapshot["boot_id"],
                        "signals": memory_termination_signals,
                    },
                )
            update_live_anomaly_episode(paths, snapshot)
        except Exception as error:  # noqa: BLE001 - monitor must survive partial sysfs failures.
            append_jsonl(
                paths.notes_log,
                {
                    "event": "heartbeat_error",
                    "timestamp": iso_now(),
                    "error": repr(error),
                },
            )
        next_run += interval_seconds
        while not stopping:
            remaining = next_run - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 1.0))
    append_jsonl(
        paths.notes_log,
        {"event": "monitor_stopped", "timestamp": iso_now(), "boot_id": current_boot_id()},
    )
    return 0


def status(paths: MonitorPaths) -> int:
    ensure_directories(paths)
    last_lines = tail_lines([paths.heartbeat_log], 1)
    last_heartbeat: dict[str, Any] | None = None
    if last_lines:
        try:
            candidate = json.loads(last_lines[0])
            last_heartbeat = candidate if isinstance(candidate, dict) else None
        except json.JSONDecodeError:
            pass
    incidents = sorted(path for path in paths.incidents_dir.iterdir() if path.is_dir())
    result = {
        "current_boot_id": current_boot_id(),
        "boot_state": read_json(paths.boot_state),
        "last_classification": read_json(paths.last_classification),
        "last_memory_termination": read_json(paths.last_memory_termination),
        "last_heartbeat": last_heartbeat,
        "incident_count": len(incidents),
        "latest_incident": str(incidents[-1]) if incidents else None,
        "heartbeat_log_bytes": (
            paths.heartbeat_log.stat().st_size if paths.heartbeat_log.exists() else 0
        ),
        "state_dir": str(paths.state_dir),
        "log_dir": str(paths.log_dir),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def list_incidents(paths: MonitorPaths) -> int:
    ensure_directories(paths)
    for incident_dir in sorted(
        (path for path in paths.incidents_dir.iterdir() if path.is_dir()),
        reverse=True,
    ):
        metadata = read_json(incident_dir / "metadata.json") or {}
        print(
            json.dumps(
                {
                    "path": str(incident_dir),
                    "created_at": metadata.get("created_at"),
                    "reason": metadata.get("reason"),
                    "classification": metadata.get("classification"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return 0


def record_note(paths: MonitorPaths, message: str) -> int:
    ensure_directories(paths)
    append_jsonl(
        paths.notes_log,
        {
            "event": "operator_note",
            "timestamp": iso_now(),
            "boot_id": current_boot_id(),
            "message": message,
        },
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="write durable heartbeat records")
    run_parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    run_parser.add_argument("--max-log-bytes", type=int, default=DEFAULT_MAX_LOG_BYTES)
    run_parser.add_argument("--generations", type=int, default=DEFAULT_LOG_GENERATIONS)

    subparsers.add_parser("boot-check", help="classify the previous boot and collect evidence")
    shutdown_parser = subparsers.add_parser(
        "mark-clean-shutdown", help="write a durable clean-shutdown marker"
    )
    shutdown_parser.add_argument("--only-if-stopping", action="store_true")
    subparsers.add_parser("status", help="show monitor and last-boot state")
    subparsers.add_parser("incidents", help="list collected incident bundles")

    collect_parser = subparsers.add_parser("collect", help="collect a manual evidence bundle")
    collect_parser.add_argument("--reason", default="manual")
    note_parser = subparsers.add_parser("note", help="append an operator note")
    note_parser.add_argument("message")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    paths = paths_from_environment()
    if arguments.command == "run":
        if arguments.interval < 5:
            raise SystemExit("--interval must be at least 5 seconds")
        return monitor_loop(
            paths,
            arguments.interval,
            arguments.max_log_bytes,
            arguments.generations,
        )
    if arguments.command == "boot-check":
        return boot_check(paths)
    if arguments.command == "mark-clean-shutdown":
        return mark_clean_shutdown(paths, arguments.only_if_stopping)
    if arguments.command == "status":
        return status(paths)
    if arguments.command == "incidents":
        return list_incidents(paths)
    if arguments.command == "collect":
        incident_dir = collect_incident(
            paths,
            arguments.reason,
            include_previous_boot=False,
        )
        print(incident_dir)
        return 0
    if arguments.command == "note":
        return record_note(paths, arguments.message)
    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
