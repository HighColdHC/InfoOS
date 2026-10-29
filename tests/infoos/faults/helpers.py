from __future__ import annotations

from pathlib import Path

from backend.application.tasks.types import Task, TaskType
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.repositories import SourceRepository


def seed_source(base_dir: Path, *, source_id: str, platform: str = "rss") -> None:
    """Create an isolated, enabled source for an acquisition fault test."""
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": platform,
            "source_type": "creator" if platform == "x" else "feed",
            "identifier": f"https://example.test/{source_id}",
            "display_name": source_id,
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": source_id,
            "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
        }
    )


def fetch_task(source_id: str, *, task_id: str | None = None) -> Task:
    return Task(
        task_id=task_id or f"fetch_{source_id}",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id=source_id,
        next_run_at="2026-07-11T00:00:00Z",
        artifacts_summary={"run_id": f"run-{source_id}", "chain_id": f"chain-{source_id}"},
    )


def raw_item(source_id: str, *, item_id: str = "remote-1") -> RawItem:
    return RawItem(
        raw_item_id=item_id,
        connector_id=source_id,
        source_platform="rss",
        source_type="article",
        source_url=f"https://example.test/{item_id}",
        captured_at="2026-07-11T00:00:00Z",
        raw_text="durable fault-injection fixture",
        raw_metadata={"source_id": source_id, "title": "Fault injection"},
    )
