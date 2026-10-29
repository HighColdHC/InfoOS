from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return utc_now_micro_z()


@dataclass
class ErrorEvent:
    error_id: str
    run_id: str
    occurred_at: str
    stage: str
    severity: str
    message: str
    connector_id: str | None = None
    raw_item_id: str | None = None
    card_id: str | None = None
    source_url: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_exception(
        cls,
        *,
        run_id: str,
        stage: str,
        severity: str,
        message: str,
        exc: Exception,
        connector_id: str | None = None,
    ) -> "ErrorEvent":
        return cls(
            error_id=f"err_{uuid4().hex[:12]}",
            run_id=run_id,
            occurred_at=utc_now(),
            stage=stage,
            severity=severity,
            connector_id=connector_id,
            message=message,
            details={"exception_type": exc.__class__.__name__},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceSummary:
    connector_id: str
    status: str
    source_id: str | None = None
    source_lane: str | None = None
    fetched_count: int = 0
    visible_count: int = 0
    new_count: int = 0
    skipped_count: int = 0
    dedup_skipped_count: int = 0
    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CardSummary:
    generated: int = 0
    scored: int = 0
    routed: int = 0
    exported: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    run_id: str
    status: str
    started_at: str
    finished_at: str | None = None
    sources: list[SourceSummary] = field(default_factory=list)
    cards: CardSummary = field(default_factory=CardSummary)
    errors: list[ErrorEvent] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls) -> "RunResult":
        return cls(run_id=f"run_{uuid4().hex[:12]}", status="success", started_at=utc_now())

    def finish(self) -> None:
        self.finished_at = utc_now()
        if self.errors and any(source.status == "success" for source in self.sources):
            self.status = "partial_failure"
        elif self.errors:
            self.status = "failed"
        else:
            self.status = "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "sources": [source.to_dict() for source in self.sources],
            "cards": self.cards.to_dict(),
            "errors": [error.to_dict() for error in self.errors],
            **self.outputs,
        }
