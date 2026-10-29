from __future__ import annotations

import importlib.util
from graphlib import TopologicalSorter
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "infoos-host-monitor.py"
MONITOR_UNIT_PATH = (
    PROJECT_ROOT / "deploy" / "linux" / "systemd" / "infoos-host-monitor.service"
)
FORENSICS_UNIT_PATH = (
    PROJECT_ROOT / "deploy" / "linux" / "systemd" / "infoos-host-forensics.service"
)


def _load_module():
    sys.path.insert(0, str(SCRIPT_PATH.parent))
    spec = importlib.util.spec_from_file_location("infoos_host_monitor", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


monitor = _load_module()
incident_storage = importlib.import_module("infoos_host_monitor_incidents")


def test_boot_classification_distinguishes_clean_and_abnormal_termination():
    previous = {"boot_id": "boot-old"}

    assert monitor.classify_boot(None, None, "boot-new")["status"] == "first_observation"
    assert monitor.classify_boot(previous, None, "boot-old")["status"] == "same_boot"
    assert (
        monitor.classify_boot(previous, {"boot_id": "boot-old"}, "boot-new")["status"]
        == "clean_shutdown"
    )
    assert (
        monitor.classify_boot(previous, {"boot_id": "another-boot"}, "boot-new")["status"]
        == "abnormal_termination"
    )


def test_durable_json_and_jsonl_are_immediately_readable(tmp_path: Path):
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"

    monitor.durable_write_json(state, {"boot_id": "boot-1"})
    monitor.append_jsonl(events, {"event": "heartbeat", "value": 1})
    monitor.append_jsonl(events, {"event": "heartbeat", "value": 2})

    assert monitor.read_json(state) == {"boot_id": "boot-1"}
    assert [json.loads(line)["value"] for line in events.read_text().splitlines()] == [1, 2]


def test_log_rotation_preserves_bounded_generations(tmp_path: Path):
    log = tmp_path / "heartbeat.jsonl"
    log.write_text("old\n")

    monitor.rotate_log(log, max_bytes=1, generations=2)
    log.write_text("new\n")
    monitor.rotate_log(log, max_bytes=1, generations=2)
    log.write_text("current\n")

    assert log.read_text() == "current\n"
    assert (tmp_path / "heartbeat.jsonl.1").read_text() == "new\n"
    assert (tmp_path / "heartbeat.jsonl.2").read_text() == "old\n"


def test_tail_lines_spans_rotated_files_in_order(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_text("1\n2\n")
    second.write_text("3\n4\n")

    assert monitor.tail_lines([first, second], 3) == ["2\n", "3\n", "4\n"]


def test_parse_proc_metrics_tolerates_unrelated_fields():
    meminfo = monitor.parse_meminfo("MemTotal: 100 kB\nIgnored: value\nMemAvailable: 50 kB\n")
    proc_stat = monitor.parse_proc_stat("cpu 1 2 3\nprocs_running 7\nprocs_blocked 2\n")

    assert meminfo == {"MemTotal": 100, "MemAvailable": 50}
    assert proc_stat == {"procs_running": 7, "procs_blocked": 2}


def test_process_memory_snapshot_records_totals_without_command_arguments(
    tmp_path: Path,
):
    process = tmp_path / "123"
    process.mkdir()
    (process / "status").write_text(
        "\n".join(
            (
                "Name:\tpython",
                "Uid:\t1000\t1000\t1000\t1000",
                "VmRSS:\t2048 kB",
                "RssAnon:\t1536 kB",
                "RssFile:\t512 kB",
                "VmSwap:\t64 kB",
            )
        )
    )

    snapshot = monitor.collect_top_process_memory(proc_root=tmp_path)

    assert snapshot == [
        {
            "pid": 123,
            "name": "python",
            "uid": 1000,
            "vmrss_kib": 2048,
            "rssanon_kib": 1536,
            "rssfile_kib": 512,
            "vmswap_kib": 64,
        }
    ]
    assert "cmdline" not in snapshot[0]


def test_heartbeat_service_has_no_network_or_linux_capabilities():
    unit = MONITOR_UNIT_PATH.read_text()

    assert "CapabilityBoundingSet=\n" in unit
    assert "PrivateNetwork=yes" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/infoos-host-monitor" in unit


def test_forensics_service_does_not_create_a_multi_user_ordering_cycle():
    # Ubuntu orders TLP after multi-user.target. Model that host relationship
    # together with both installed units, including the target's default ordering
    # after services pulled in by WantedBy.
    predecessors = {"tlp.service": {"multi-user.target"}}
    for path in (MONITOR_UNIT_PATH, FORENSICS_UNIT_PATH):
        values = {}
        section = ""
        unit = re.sub(r"\\\n\s*", " ", path.read_text())
        for line in unit.splitlines():
            line = line.strip()
            if line.startswith("["):
                section = line[1:-1]
            elif line and not line.startswith(("#", ";")):
                key, value = line.split("=", 1)
                entries = values.setdefault((section, key.strip()), [])
                if value.strip():
                    entries.extend(value.split())
                else:
                    entries.clear()
        predecessors.setdefault(path.name, set()).update(values.get(("Unit", "After"), []))
        for successor in values.get(("Unit", "Before"), []):
            predecessors.setdefault(successor, set()).add(path.name)
        for target in values.get(("Install", "WantedBy"), []):
            predecessors.setdefault(target, set()).add(path.name)

    TopologicalSorter(predecessors).prepare()  # Raises CycleError on a boot ordering loop.


def test_anomaly_detection_identifies_memory_termination_signals():
    previous = {
        "boot_id": "boot-1",
        "memory_pressure": {"full": {"avg10": 0.0}},
        "memory_control": {
            "system_slice": {"events": {"oom": 0, "oom_kill": 0}},
            "containers": [
                {
                    "name": "infoos-acceptance",
                    "exists": True,
                    "id": "container-1",
                    "status": "running",
                    "oom_killed": False,
                    "exit_code": 0,
                    "restart_count": 0,
                    "events": {"oom": 0, "oom_kill": 0},
                }
            ],
        },
    }
    current = {
        "boot_id": "boot-1",
        "memory_kib": {},
        "scheduler": {},
        "disk_free_bytes": {},
        "watchdog": {},
        "kernel_watchdogs": {"nmi_watchdog": "1"},
        "memory_pressure": {"full": {"avg10": 1.25}},
        "memory_control": {
            "system_slice": {"events": {"oom": 1, "oom_kill": 1}},
            "containers": [
                {
                    "name": "infoos-acceptance",
                    "exists": True,
                    "id": "container-1",
                    "status": "exited",
                    "oom_killed": True,
                    "exit_code": 137,
                    "restart_count": 1,
                    "events": {"oom": 1, "oom_kill": 1},
                }
            ],
        },
    }

    kinds = {
        item["kind"] for item in monitor.detect_anomalies(current, previous_snapshot=previous)
    }

    assert kinds == {
        "cgroup_oom",
        "cgroup_oom_kill",
        "container_oom_killed",
        "container_restart_count_increased",
        "high_memory_pressure",
    }


def test_transient_memory_reclaim_is_recorded_without_opening_an_incident():
    previous = {
        "boot_id": "boot-1",
        "memory_pressure": {"full": {"avg10": 0.0}},
        "memory_pressure_assessment": {"high_sample_streak": 0},
        "memory_control": {"system_slice": {"events": {}}, "containers": []},
    }
    current = {
        "boot_id": "boot-1",
        "memory_kib": {
            "MemTotal": 32 * 1024 * 1024,
            "MemAvailable": 23 * 1024 * 1024,
        },
        "memory_pressure": {"full": {"avg10": 8.12}},
        "memory_control": {"system_slice": {"events": {}}, "containers": []},
    }

    assessment = monitor.assess_memory_pressure(
        current,
        previous_snapshot=previous,
    )
    kinds = {
        item["kind"]
        for item in monitor.detect_anomalies(current, previous_snapshot=previous)
    }

    assert assessment["classification"] == "transient_reclaim"
    assert assessment["high_sample_streak"] == 1
    assert assessment["incident_worthy"] is False
    assert "high_memory_pressure" not in kinds


def test_sustained_memory_pressure_opens_an_incident_after_four_samples():
    previous = {
        "boot_id": "boot-1",
        "memory_pressure": {"full": {"avg10": 2.0}},
        "memory_pressure_assessment": {"high_sample_streak": 3},
        "memory_control": {"system_slice": {"events": {}}, "containers": []},
    }
    current = {
        "boot_id": "boot-1",
        "memory_kib": {
            "MemTotal": 32 * 1024 * 1024,
            "MemAvailable": 20 * 1024 * 1024,
        },
        "memory_pressure": {"full": {"avg10": 2.5}},
        "memory_control": {"system_slice": {"events": {}}, "containers": []},
    }

    assessment = monitor.assess_memory_pressure(
        current,
        previous_snapshot=previous,
    )
    anomalies = monitor.detect_anomalies(current, previous_snapshot=previous)

    assert assessment["classification"] == "sustained_pressure"
    assert assessment["high_sample_streak"] == 4
    assert assessment["incident_worthy"] is True
    assert any(item["kind"] == "high_memory_pressure" for item in anomalies)


def test_anomaly_detection_covers_hardware_and_power_regressions():
    snapshot = {
        "temperatures": [
            {"device": "k10temp", "millidegrees_c": 96_000},
            {"device": "nvme", "millidegrees_c": 86_000},
        ],
        "memory_kib": {
            "HardwareCorrupted": 4,
            "MemAvailable": 256 * 1024,
            "SwapTotal": 1024 * 1024,
            "SwapFree": 64 * 1024,
        },
        "scheduler": {"procs_blocked": 9},
        "disk_free_bytes": {"state": 1024},
        "watchdog": {"timeleft_seconds": "8"},
        "kernel_watchdogs": {"nmi_watchdog": "0"},
        "nvme_power": [{"name": "nvme0", "control": "auto"}],
    }

    kinds = {item["kind"] for item in monitor.detect_anomalies(snapshot)}

    assert kinds == {
        "high_temperature",
        "hardware_corrupted_memory",
        "low_available_memory",
        "low_swap",
        "blocked_processes",
        "low_disk_space",
        "watchdog_near_expiry",
        "nmi_watchdog_disabled",
        "nvme_runtime_power_enabled",
    }


def test_live_anomaly_is_deduplicated_until_a_recovery(
    tmp_path: Path,
    monkeypatch,
):
    paths = monitor.MonitorPaths(tmp_path / "state", tmp_path / "logs")
    monitor.ensure_directories(paths)
    collected: list[str] = []

    def fake_collect(_paths, reason, **_kwargs):
        collected.append(reason)
        incident = paths.incidents_dir / f"incident-{len(collected)}"
        incident.mkdir()
        return incident

    monkeypatch.setattr(monitor, "collect_incident", fake_collect)
    anomaly = {
        "timestamp": "2026-07-27T00:00:00+00:00",
        "boot_id": "boot-1",
        "anomalies": [{"kind": "high_temperature"}],
    }

    monitor.update_live_anomaly_episode(paths, anomaly)
    monitor.update_live_anomaly_episode(
        paths,
        {**anomaly, "timestamp": "2026-07-27T01:00:00+00:00"},
    )
    assert collected == ["live-anomaly-high_temperature"]
    assert monitor.read_json(paths.state_dir / "last-live-incident.json")["active"] is True

    monitor.update_live_anomaly_episode(
        paths,
        {
            "timestamp": "2026-07-27T01:01:00+00:00",
            "boot_id": "boot-1",
            "anomalies": [],
        },
    )
    assert monitor.read_json(paths.state_dir / "last-live-incident.json")["active"] is False

    monitor.update_live_anomaly_episode(
        paths,
        {**anomaly, "timestamp": "2026-07-27T01:02:00+00:00"},
    )
    assert collected == [
        "live-anomaly-high_temperature",
        "live-anomaly-high_temperature",
    ]


def test_incident_collection_bounds_each_artifact_and_the_bundle(
    tmp_path: Path,
    monkeypatch,
):
    paths = monitor.MonitorPaths(tmp_path / "state", tmp_path / "logs")
    monitor.ensure_directories(paths)
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENTS", "3")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENT_BYTES", "8192")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_TOTAL_INCIDENT_BYTES", "16384")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_KEEP_FREE_BYTES", "1")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_ARTIFACT_BYTES", "256")
    monkeypatch.setattr(
        incident_storage.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**9, used=0, free=10**9),
    )
    monkeypatch.setattr(monitor, "run_command", lambda *_args, **_kwargs: "x" * 100_000)
    monkeypatch.setattr(monitor, "glob", SimpleNamespace(glob=lambda _pattern: []))
    monkeypatch.setattr(monitor, "current_boot_id", lambda: "boot-1")

    incident = monitor.collect_incident(
        paths,
        "bounded-test",
        include_previous_boot=False,
    )

    artifact_sizes = [
        path.stat().st_size
        for path in incident.rglob("*")
        if path.is_file() and path.name != "metadata.json"
    ]
    assert artifact_sizes
    assert max(artifact_sizes) <= 256
    assert incident_storage.directory_bytes(incident) <= 8192
    assert any(
        "truncated by infoos-host-monitor" in path.read_text(errors="replace")
        for path in incident.iterdir()
        if path.is_file() and path.name != "metadata.json"
    )


def test_incident_retention_removes_oldest_real_directory_and_skips_symlink(
    tmp_path: Path,
    monkeypatch,
):
    paths = monitor.MonitorPaths(tmp_path / "state", tmp_path / "logs")
    monitor.ensure_directories(paths)
    oldest = paths.incidents_dir / "001-oldest"
    newest = paths.incidents_dir / "002-newest"
    oldest.mkdir()
    newest.mkdir()
    (oldest / "payload").write_bytes(b"a" * 10)
    (newest / "payload").write_bytes(b"b" * 10)
    oldest.touch()
    newest.touch()
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink = paths.incidents_dir / "000-unsafe-link"
    symlink.symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENTS", "1")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENT_BYTES", "100")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_TOTAL_INCIDENT_BYTES", "100")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_KEEP_FREE_BYTES", "1")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_ARTIFACT_BYTES", "50")
    monkeypatch.setattr(
        incident_storage.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**9, used=0, free=10**9),
    )

    monitor.prune_incidents(paths)

    assert not oldest.exists()
    assert newest.exists()
    assert symlink.is_symlink()
    assert outside.exists()


def test_incident_retention_refuses_to_cross_keep_free_reserve(
    tmp_path: Path,
    monkeypatch,
):
    paths = monitor.MonitorPaths(tmp_path / "state", tmp_path / "logs")
    monitor.ensure_directories(paths)
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENTS", "2")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENT_BYTES", "100")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_TOTAL_INCIDENT_BYTES", "200")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_KEEP_FREE_BYTES", "1000")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_ARTIFACT_BYTES", "50")
    monkeypatch.setattr(
        incident_storage.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=2000, used=1501, free=499),
    )

    try:
        monitor.prune_incidents(paths, reserve_bytes=100)
    except RuntimeError as error:
        assert "keep-free" in str(error)
    else:
        raise AssertionError("expected keep-free guard to reject incident collection")


def test_incident_retention_enforces_total_byte_budget(
    tmp_path: Path,
    monkeypatch,
):
    paths = monitor.MonitorPaths(tmp_path / "state", tmp_path / "logs")
    monitor.ensure_directories(paths)
    oldest = paths.incidents_dir / "001-oldest"
    newest = paths.incidents_dir / "002-newest"
    oldest.mkdir()
    (oldest / "payload").write_bytes(b"a" * 10)
    newest.mkdir()
    (newest / "payload").write_bytes(b"b" * 10)
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENTS", "10")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_INCIDENT_BYTES", "10")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_TOTAL_INCIDENT_BYTES", "15")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_KEEP_FREE_BYTES", "1")
    monkeypatch.setenv("INFOOS_HOST_MONITOR_MAX_ARTIFACT_BYTES", "10")
    monkeypatch.setattr(
        incident_storage.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10**9, used=0, free=10**9),
    )

    monitor.prune_incidents(paths)

    assert not oldest.exists()
    assert newest.exists()
    assert incident_storage.directory_bytes(paths.incidents_dir) == 10


def test_bundle_copy_skips_oversized_artifact_without_partial_output(tmp_path: Path):
    source = tmp_path / "large.bin"
    destination = tmp_path / "bundle" / "large.bin"
    destination.parent.mkdir()
    source.write_bytes(b"x" * 11)

    copied = incident_storage.copy_bundle_file(
        source,
        destination,
        remaining_bytes=10,
        artifact_limit=10,
    )

    assert copied == 0
    assert not destination.exists()
