"""Safe versioned facts used by durable recovery events."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def safe_identifier(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_ID.fullmatch(normalized):
        raise ValueError(f"unsafe {field}")
    return normalized


@dataclass(frozen=True)
class FailureFact:
    failure_class: str
    scope_kind: str
    scope_key: str
    retry_after_seconds: int | None = None
    version: int = 1

    def __post_init__(self) -> None:
        safe_identifier(self.failure_class, field="failure_class")
        if self.scope_kind not in {"task", "platform", "provider", "resource"}:
            raise ValueError("unsafe scope_kind")
        safe_identifier(self.scope_key, field="scope_key")

    def to_event_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str
    next_run_at: str | None = None
    circuit_state: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        safe_identifier(self.action, field="action")
        safe_identifier(self.reason, field="reason")
        if self.circuit_state not in {None, "closed", "open", "half_open"}:
            raise ValueError("unsafe circuit_state")

    def to_event_payload(self) -> dict[str, Any]:
        return asdict(self)
