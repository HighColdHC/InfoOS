"""Read-only Linux memory-pressure snapshot used by task resource admission."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePressureSnapshot:
    memory_full_avg10: float | None
    mem_available_bytes: int | None
    cgroup_current_bytes: int | None
    cgroup_high_bytes: int | None = None
    cgroup_max_bytes: int | None = None


def collect_runtime_pressure(
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> RuntimePressureSnapshot:
    return RuntimePressureSnapshot(
        memory_full_avg10=_memory_full_avg10(
            _read_text(proc_root / "pressure" / "memory")
        ),
        mem_available_bytes=_mem_available_bytes(
            _read_text(proc_root / "meminfo")
        ),
        cgroup_current_bytes=_read_limit(cgroup_root / "memory.current"),
        cgroup_high_bytes=_read_limit(cgroup_root / "memory.high"),
        cgroup_max_bytes=_read_limit(cgroup_root / "memory.max"),
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _read_limit(path: Path) -> int | None:
    value = _read_text(path).strip()
    return int(value) if value.isdigit() else None


def _memory_full_avg10(text: str) -> float | None:
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0] != "full":
            continue
        for field in fields[1:]:
            key, separator, value = field.partition("=")
            if separator and key == "avg10":
                try:
                    return float(value)
                except ValueError:
                    return None
    return None


def _mem_available_bytes(text: str) -> int | None:
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if separator and key == "MemAvailable":
            fields = value.strip().split()
            if fields and fields[0].isdigit():
                return int(fields[0]) * 1024
    return None
