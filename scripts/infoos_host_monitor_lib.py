"""Low-level persistence and host-snapshot helpers for infoos-host-monitor."""

from __future__ import annotations

import collections
import datetime as dt
import glob
import http.client
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_STATE_DIR = Path("/var/lib/infoos-host-monitor")
DEFAULT_LOG_DIR = Path("/var/log/infoos-hardware-monitor")
DEFAULT_INTERVAL_SECONDS = 15
DEFAULT_MAX_LOG_BYTES = 64 * 1024 * 1024
DEFAULT_LOG_GENERATIONS = 6
DEFAULT_MONITORED_CONTAINERS = ("infoos-acceptance", "we-mp-rss")


@dataclass(frozen=True)
class MonitorPaths:
    state_dir: Path
    log_dir: Path

    @property
    def heartbeat_log(self) -> Path:
        return self.log_dir / "heartbeat.jsonl"

    @property
    def notes_log(self) -> Path:
        return self.log_dir / "notes.jsonl"

    @property
    def boot_state(self) -> Path:
        return self.state_dir / "boot-state.json"

    @property
    def clean_marker(self) -> Path:
        return self.state_dir / "clean-shutdown.json"

    @property
    def last_classification(self) -> Path:
        return self.state_dir / "last-boot-classification.json"

    @property
    def last_memory_termination(self) -> Path:
        return self.state_dir / "last-memory-termination.json"

    @property
    def incidents_dir(self) -> Path:
        return self.state_dir / "incidents"

    @property
    def boots_dir(self) -> Path:
        return self.state_dir / "boots"


def paths_from_environment() -> MonitorPaths:
    return MonitorPaths(
        state_dir=Path(os.environ.get("INFOOS_HOST_MONITOR_STATE_DIR", DEFAULT_STATE_DIR)),
        log_dir=Path(os.environ.get("INFOOS_HOST_MONITOR_LOG_DIR", DEFAULT_LOG_DIR)),
    )


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="milliseconds")


def ensure_directories(paths: MonitorPaths) -> None:
    for path, mode in (
        (paths.state_dir, 0o700),
        (paths.log_dir, 0o750),
        (paths.incidents_dir, 0o700),
        (paths.boots_dir, 0o700),
    ):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, mode)


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_write_text(path: Path, value: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def durable_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    durable_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode,
    )


def append_jsonl(path: Path, value: Any, mode: int = 0o640) -> None:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, mode)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def read_text(path: Path, default: str | None = None) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def current_boot_id() -> str:
    value = read_text(Path("/proc/sys/kernel/random/boot_id"))
    if not value:
        raise RuntimeError("unable to read the current boot ID")
    return value


def parse_meminfo(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        fields = remainder.strip().split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0])
    return values


def parse_proc_stat(text: str) -> dict[str, int]:
    wanted = {"procs_running", "procs_blocked"}
    values: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] in wanted and fields[1].isdigit():
            values[fields[0]] = int(fields[1])
    return values


def parse_key_value_counters(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2:
            try:
                values[fields[0]] = int(fields[1])
            except ValueError:
                continue
    return values


def parse_pressure(text: str) -> dict[str, dict[str, float | int]]:
    pressure: dict[str, dict[str, float | int]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        metrics: dict[str, float | int] = {}
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            if not separator:
                continue
            try:
                metrics[key] = int(value) if key == "total" else float(value)
            except ValueError:
                continue
        pressure[fields[0]] = metrics
    return pressure


def collect_cgroup_memory(directory: Path) -> dict[str, Any]:
    current = read_text(directory / "memory.current")
    maximum = read_text(directory / "memory.max")
    return {
        "path": str(directory),
        "current_bytes": int(current) if current and current.isdigit() else None,
        "max_bytes": int(maximum) if maximum and maximum.isdigit() else maximum,
        "events": parse_key_value_counters(read_text(directory / "memory.events", "") or ""),
        "stat": parse_key_value_counters(
            read_text(directory / "memory.stat", "") or ""
        ),
        "pressure": parse_pressure(read_text(directory / "memory.pressure", "") or ""),
    }


def parse_process_status(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    memory_keys = {"VmRSS", "RssAnon", "RssFile", "VmSwap"}
    for line in text.splitlines():
        key, separator, remainder = line.partition(":")
        if not separator:
            continue
        fields = remainder.strip().split()
        if key == "Name" and fields:
            result["name"] = fields[0]
        elif key == "Uid" and fields and fields[0].isdigit():
            result["uid"] = int(fields[0])
        elif key in memory_keys and fields and fields[0].isdigit():
            result[f"{key.lower()}_kib"] = int(fields[0])
    return result


def collect_top_process_memory(
    *,
    proc_root: Path = Path("/proc"),
    limit: int = 20,
) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    try:
        candidates = proc_root.iterdir()
    except OSError:
        return processes
    for directory in candidates:
        if not directory.name.isdigit():
            continue
        status = parse_process_status(
            read_text(directory / "status", "") or ""
        )
        rss = status.get("vmrss_kib")
        if not isinstance(rss, int) or rss <= 0:
            continue
        processes.append({"pid": int(directory.name), **status})
    processes.sort(
        key=lambda item: (
            int(item.get("vmrss_kib") or 0),
            int(item.get("rssanon_kib") or 0),
        ),
        reverse=True,
    )
    return processes[: max(0, limit)]


def monitored_container_names() -> tuple[str, ...]:
    configured = os.environ.get("INFOOS_HOST_MONITOR_CONTAINERS")
    if not configured:
        return DEFAULT_MONITORED_CONTAINERS
    return tuple(name.strip() for name in configured.split(",") if name.strip())


class _DockerSocketConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("localhost", timeout=5)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


def collect_container_memory(name: str) -> dict[str, Any]:
    try:
        connection = _DockerSocketConnection()
        connection.request(
            "GET",
            f"/containers/{urllib.parse.quote(name, safe='')}/json",
        )
        response = connection.getresponse()
        body = response.read()
        connection.close()
        if response.status != 200:
            return {
                "name": name,
                "exists": False,
                "error": f"docker_api_status={response.status}",
            }
        details = json.loads(body)
    except (OSError, http.client.HTTPException, json.JSONDecodeError) as error:
        return {"name": name, "exists": False, "error": repr(error)}
    if not isinstance(details, dict):
        return {"name": name, "exists": False, "error": "invalid Docker API payload"}
    state = details.get("State") if isinstance(details.get("State"), dict) else {}
    container_id = str(details.get("Id") or "")
    cgroup = collect_cgroup_memory(
        Path("/sys/fs/cgroup/system.slice") / f"docker-{container_id}.scope"
    )
    health = state.get("Health") if isinstance(state.get("Health"), dict) else {}
    return {
        "name": name,
        "exists": True,
        "id": container_id,
        "status": state.get("Status"),
        "running": state.get("Running"),
        "oom_killed": state.get("OOMKilled"),
        "exit_code": state.get("ExitCode"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "health": health.get("Status"),
        "restart_count": details.get("RestartCount"),
        **cgroup,
    }


def collect_power_supplies() -> list[dict[str, str]]:
    supplies: list[dict[str, str]] = []
    for directory_name in sorted(glob.glob("/sys/class/power_supply/*")):
        directory = Path(directory_name)
        supply: dict[str, str] = {"name": directory.name}
        for field in ("type", "online", "status", "capacity", "health"):
            value = read_text(directory / field)
            if value is not None:
                supply[field] = value
        supplies.append(supply)
    return supplies


def collect_temperatures() -> list[dict[str, Any]]:
    temperatures: list[dict[str, Any]] = []
    for directory_name in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        directory = Path(directory_name)
        device_name = read_text(directory / "name", directory.name)
        for input_name in sorted(directory.glob("temp*_input")):
            raw = read_text(input_name)
            try:
                value = int(raw) if raw is not None else None
            except ValueError:
                value = None
            if value is None or value < -100_000 or value > 200_000:
                continue
            prefix = input_name.name.removesuffix("_input")
            temperatures.append(
                {
                    "device": device_name,
                    "label": read_text(directory / f"{prefix}_label", prefix),
                    "millidegrees_c": value,
                }
            )
    return temperatures


def collect_nvme_power() -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    for device_name in sorted(glob.glob("/sys/class/nvme/nvme*")):
        device = Path(device_name)
        devices.append(
            {
                "name": device.name,
                "control": read_text(device / "device/power/control", "unknown") or "unknown",
                "runtime_status": (
                    read_text(device / "device/power/runtime_status", "unknown") or "unknown"
                ),
            }
        )
    return devices


def disk_free_bytes(path: Path) -> int | None:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def detect_anomalies(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    for temperature in snapshot.get("temperatures", []):
        device = str(temperature.get("device", "unknown"))
        value = temperature.get("millidegrees_c")
        threshold = 85_000 if device == "nvme" else 95_000
        if isinstance(value, int) and value >= threshold:
            anomalies.append(
                {
                    "kind": "high_temperature",
                    "device": device,
                    "value": value,
                    "threshold": threshold,
                }
            )

    memory = snapshot.get("memory_kib", {})
    if (memory.get("HardwareCorrupted") or 0) > 0:
        anomalies.append(
            {
                "kind": "hardware_corrupted_memory",
                "value": memory["HardwareCorrupted"],
            }
        )
    mem_available = memory.get("MemAvailable")
    if isinstance(mem_available, int) and mem_available < 512 * 1024:
        anomalies.append({"kind": "low_available_memory", "value": mem_available})
    swap_total = memory.get("SwapTotal") or 0
    swap_free = memory.get("SwapFree")
    if swap_total and isinstance(swap_free, int) and swap_free < 128 * 1024:
        anomalies.append({"kind": "low_swap", "value": swap_free})

    pressure_assessment = assess_memory_pressure(
        snapshot,
        previous_snapshot=previous_snapshot,
    )
    if pressure_assessment["incident_worthy"]:
        anomalies.append(
            {
                "kind": "high_memory_pressure",
                "value": pressure_assessment["full_avg10"],
                "threshold": pressure_assessment["threshold"],
                "classification": pressure_assessment["classification"],
                "high_sample_streak": pressure_assessment[
                    "high_sample_streak"
                ],
            }
        )

    current_control = snapshot.get("memory_control", {})
    previous_control = (
        previous_snapshot.get("memory_control", {})
        if previous_snapshot and previous_snapshot.get("boot_id") == snapshot.get("boot_id")
        else {}
    )

    def add_cgroup_deltas(
        scope: str,
        current_scope: dict[str, Any],
        previous_scope: dict[str, Any],
    ) -> None:
        current_events = current_scope.get("events", {})
        previous_events = previous_scope.get("events", {})
        for counter, kind in (("oom", "cgroup_oom"), ("oom_kill", "cgroup_oom_kill")):
            current_value = current_events.get(counter)
            previous_value = previous_events.get(counter, 0)
            if (
                isinstance(current_value, int)
                and isinstance(previous_value, int)
                and current_value > previous_value
            ):
                anomalies.append(
                    {
                        "kind": kind,
                        "scope": scope,
                        "value": current_value,
                        "delta": current_value - previous_value,
                    }
                )

    add_cgroup_deltas(
        "system.slice",
        current_control.get("system_slice", {}),
        previous_control.get("system_slice", {}),
    )
    previous_containers = {
        item.get("name"): item
        for item in previous_control.get("containers", [])
        if isinstance(item, dict)
    }
    for container in current_control.get("containers", []):
        if not isinstance(container, dict) or not container.get("exists"):
            continue
        name = str(container.get("name"))
        previous = previous_containers.get(name, {})
        same_container = previous.get("id") == container.get("id")
        baseline = previous if same_container else {}
        add_cgroup_deltas(name, container, baseline)
        if container.get("oom_killed") and not baseline.get("oom_killed"):
            anomalies.append(
                {
                    "kind": "container_oom_killed",
                    "container": name,
                    "exit_code": container.get("exit_code"),
                }
            )
        elif (
            container.get("status") in {"dead", "exited"}
            and container.get("exit_code") == 137
            and not (
                baseline.get("status") in {"dead", "exited"}
                and baseline.get("exit_code") == 137
            )
        ):
            anomalies.append(
                {
                    "kind": "container_sigkill_exit",
                    "container": name,
                    "exit_code": 137,
                }
            )
        restart_count = container.get("restart_count")
        previous_restarts = baseline.get("restart_count", 0)
        if (
            isinstance(restart_count, int)
            and isinstance(previous_restarts, int)
            and restart_count > previous_restarts
        ):
            anomalies.append(
                {
                    "kind": "container_restart_count_increased",
                    "container": name,
                    "value": restart_count,
                    "delta": restart_count - previous_restarts,
                }
            )

    blocked = snapshot.get("scheduler", {}).get("procs_blocked")
    if isinstance(blocked, int) and blocked >= 8:
        anomalies.append({"kind": "blocked_processes", "value": blocked, "threshold": 8})

    for name, free_bytes in snapshot.get("disk_free_bytes", {}).items():
        if isinstance(free_bytes, int) and free_bytes < 5 * 1024**3:
            anomalies.append({"kind": "low_disk_space", "path": name, "value": free_bytes})

    watchdog = snapshot.get("watchdog", {})
    try:
        timeleft = int(watchdog.get("timeleft_seconds", ""))
    except (TypeError, ValueError):
        timeleft = None
    if timeleft is not None and timeleft <= 10:
        anomalies.append({"kind": "watchdog_near_expiry", "value": timeleft})

    kernel_watchdogs = snapshot.get("kernel_watchdogs", {})
    if kernel_watchdogs.get("nmi_watchdog") != "1":
        anomalies.append(
            {
                "kind": "nmi_watchdog_disabled",
                "value": kernel_watchdogs.get("nmi_watchdog"),
            }
        )
    for device in snapshot.get("nvme_power", []):
        if device.get("control") != "on":
            anomalies.append(
                {
                    "kind": "nvme_runtime_power_enabled",
                    "device": device.get("name"),
                    "value": device.get("control"),
                }
            )
    return anomalies


def assess_memory_pressure(
    snapshot: dict[str, Any],
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    threshold = 1.0
    full_pressure = snapshot.get("memory_pressure", {}).get("full", {}).get("avg10")
    high = (
        isinstance(full_pressure, (int, float))
        and full_pressure >= threshold
    )
    same_boot = (
        previous_snapshot is not None
        and previous_snapshot.get("boot_id") == snapshot.get("boot_id")
    )
    previous_streak = 0
    if same_boot and previous_snapshot is not None:
        previous_assessment = previous_snapshot.get(
            "memory_pressure_assessment",
            {},
        )
        previous_value = previous_assessment.get("high_sample_streak")
        if isinstance(previous_value, int):
            previous_streak = previous_value
        else:
            previous_pressure = (
                previous_snapshot.get("memory_pressure", {})
                .get("full", {})
                .get("avg10")
            )
            previous_streak = int(
                isinstance(previous_pressure, (int, float))
                and previous_pressure >= threshold
            )
    streak = previous_streak + 1 if high else 0

    memory = snapshot.get("memory_kib", {})
    available = memory.get("MemAvailable")
    total = memory.get("MemTotal")
    low_headroom = (
        isinstance(available, int)
        and isinstance(total, int)
        and total > 0
        and available < max(512 * 1024, int(total * 0.10))
    )
    oom_signal = _has_new_memory_termination_signal(
        snapshot,
        previous_snapshot=previous_snapshot,
    )

    if oom_signal:
        classification = "oom_risk"
    elif high and low_headroom:
        classification = "capacity_pressure"
    elif high and streak >= 4:
        classification = "sustained_pressure"
    elif high:
        classification = "transient_reclaim"
    else:
        classification = "normal"
    return {
        "classification": classification,
        "full_avg10": full_pressure,
        "threshold": threshold,
        "high_sample_streak": streak,
        "low_headroom": low_headroom,
        "oom_signal": oom_signal,
        "incident_worthy": classification
        in {"oom_risk", "capacity_pressure", "sustained_pressure"},
    }


def _has_new_memory_termination_signal(
    snapshot: dict[str, Any],
    *,
    previous_snapshot: dict[str, Any] | None,
) -> bool:
    current_control = snapshot.get("memory_control", {})
    previous_control = (
        previous_snapshot.get("memory_control", {})
        if previous_snapshot
        and previous_snapshot.get("boot_id") == snapshot.get("boot_id")
        else {}
    )

    def increased(current: dict[str, Any], previous: dict[str, Any]) -> bool:
        current_events = current.get("events", {})
        previous_events = previous.get("events", {})
        return any(
            isinstance(current_events.get(counter), int)
            and current_events.get(counter, 0)
            > previous_events.get(counter, 0)
            for counter in ("oom", "oom_kill")
        )

    if increased(
        current_control.get("system_slice", {}),
        previous_control.get("system_slice", {}),
    ):
        return True
    previous_containers = {
        item.get("name"): item
        for item in previous_control.get("containers", [])
        if isinstance(item, dict)
    }
    for container in current_control.get("containers", []):
        if not isinstance(container, dict) or not container.get("exists"):
            continue
        previous = previous_containers.get(container.get("name"), {})
        baseline = previous if previous.get("id") == container.get("id") else {}
        if increased(container, baseline):
            return True
        if container.get("oom_killed") and not baseline.get("oom_killed"):
            return True
    return False


def collect_snapshot(
    paths: MonitorPaths,
    event: str = "heartbeat",
    previous_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    load_fields = (read_text(Path("/proc/loadavg"), "") or "").split()
    uptime_fields = (read_text(Path("/proc/uptime"), "") or "").split()
    meminfo = parse_meminfo(read_text(Path("/proc/meminfo"), "") or "")
    proc_stat = parse_proc_stat(read_text(Path("/proc/stat"), "") or "")

    snapshot = {
        "schema_version": 1,
        "event": event,
        "timestamp": iso_now(),
        "epoch_ns": time.time_ns(),
        "boot_id": current_boot_id(),
        "uptime_seconds": float(uptime_fields[0]) if uptime_fields else None,
        "load": [float(value) for value in load_fields[:3]] if len(load_fields) >= 3 else [],
        "scheduler": {
            "running_total": load_fields[3] if len(load_fields) >= 4 else None,
            **proc_stat,
        },
        "memory_kib": {
            key: meminfo.get(key)
            for key in (
                "MemTotal",
                "MemAvailable",
                "SwapTotal",
                "SwapFree",
                "HardwareCorrupted",
            )
        },
        "memory_pressure": parse_pressure(
            read_text(Path("/proc/pressure/memory"), "") or ""
        ),
        "memory_control": {
            "system_slice": collect_cgroup_memory(Path("/sys/fs/cgroup/system.slice")),
            "containers": [
                collect_container_memory(name) for name in monitored_container_names()
            ],
        },
        "top_process_memory": collect_top_process_memory(),
        "power_supplies": collect_power_supplies(),
        "temperatures": collect_temperatures(),
        "platform_profile": read_text(Path("/sys/firmware/acpi/platform_profile")),
        "pcie_aspm_policy": read_text(Path("/sys/module/pcie_aspm/parameters/policy")),
        "nvme_power": collect_nvme_power(),
        "watchdog": {
            "identity": read_text(Path("/sys/class/watchdog/watchdog0/identity")),
            "timeout_seconds": read_text(Path("/sys/class/watchdog/watchdog0/timeout")),
            "timeleft_seconds": read_text(Path("/sys/class/watchdog/watchdog0/timeleft")),
        },
        "kernel_watchdogs": {
            "nmi_watchdog": read_text(Path("/proc/sys/kernel/nmi_watchdog")),
            "softlockup_panic": read_text(Path("/proc/sys/kernel/softlockup_panic")),
            "hardlockup_panic": read_text(Path("/proc/sys/kernel/hardlockup_panic")),
        },
        "disk_free_bytes": {
            "state": disk_free_bytes(paths.state_dir),
            "log": disk_free_bytes(paths.log_dir),
        },
    }
    snapshot["memory_pressure_assessment"] = assess_memory_pressure(
        snapshot,
        previous_snapshot=previous_snapshot,
    )
    snapshot["anomalies"] = detect_anomalies(
        snapshot,
        previous_snapshot=previous_snapshot,
    )
    return snapshot


def rotate_log(path: Path, max_bytes: int, generations: int) -> None:
    try:
        if path.stat().st_size < max_bytes:
            return
    except FileNotFoundError:
        return
    oldest = path.with_name(f"{path.name}.{generations}")
    oldest.unlink(missing_ok=True)
    for generation in range(generations - 1, 0, -1):
        source = path.with_name(f"{path.name}.{generation}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{generation + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))
    fsync_directory(path.parent)


def tail_lines(paths: Iterable[Path], line_count: int) -> list[str]:
    result: collections.deque[str] = collections.deque(maxlen=line_count)
    for path in paths:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                result.extend(handle)
        except OSError:
            continue
    return list(result)


def classify_boot(
    previous_state: dict[str, Any] | None,
    clean_marker: dict[str, Any] | None,
    new_boot_id: str,
) -> dict[str, Any]:
    previous_boot_id = previous_state.get("boot_id") if previous_state else None
    if not previous_boot_id:
        status = "first_observation"
    elif previous_boot_id == new_boot_id:
        status = "same_boot"
    elif clean_marker and clean_marker.get("boot_id") == previous_boot_id:
        status = "clean_shutdown"
    else:
        status = "abnormal_termination"
    return {
        "status": status,
        "previous_boot_id": previous_boot_id,
        "current_boot_id": new_boot_id,
        "clean_marker": clean_marker,
    }


def run_command(command: list[str], timeout: int = 30) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        return f"command failed: {error}\n"
    return f"exit_code={completed.returncode}\n{completed.stdout}"


def copy_if_present(source: Path, target_dir: Path) -> None:
    if not source.is_file():
        return
    try:
        shutil.copy2(source, target_dir / source.name)
    except OSError:
        return


def incident_name(reason: str) -> str:
    timestamp = utc_now().strftime("%Y%m%dT%H%M%S.%fZ")
    safe_reason = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in reason
    )
    return f"{timestamp}-{safe_reason[:80]}"
