"""Owner-bound half-open probe renewal for a claimed task heartbeat."""
from __future__ import annotations

import threading
from pathlib import Path

from backend.infrastructure.storage.runtime_circuits import (
    CircuitPolicy,
    RuntimeCircuitRepository,
)


class ProbeHeartbeat:
    def __init__(self, base_path: Path):
        self._circuits = RuntimeCircuitRepository(base_path)
        self._lock = threading.Lock()
        self._owned: tuple[str, str, str, int] | None = None
        self._initial_duration = CircuitPolicy.from_environment("platform").probe_lease_seconds

    def bind(self, scope_kind: str, scope_key: str, owner_token: str) -> None:
        duration = CircuitPolicy.from_environment(scope_kind).probe_lease_seconds
        with self._lock:
            self._owned = (scope_kind, scope_key, owner_token, duration)

    def renew(self, now: str) -> bool:
        # Serialize renewal with closing/reopening the circuit after a short probe.
        with self._lock:
            if self._owned is None:
                return True
            kind, key, token, duration = self._owned
            return self._circuits.renew_probe(
                kind, key, owner_token=token, now=now, lease_seconds=duration,
            )

    def finish(self, effect) -> None:
        with self._lock:
            effect()
            self._owned = None

    def interval_seconds(self) -> float | None:
        with self._lock:
            owned = self._owned
        duration = owned[3] if owned is not None else self._initial_duration
        return max(0.25, duration / 3)
