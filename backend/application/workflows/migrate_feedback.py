"""One-time migration of legacy feedback JSONL rows into SQLite user actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.application.usecases.feedback import resolve_feedback_topic
from backend.domain.feedback.events import ALLOWED_ACTIONS
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.infrastructure.storage.repositories import UserActionRepository


def migrate_legacy_feedback(base_dir: Path | str) -> dict[str, int]:
    """Copy legacy feedback once, retaining event ids for idempotent references."""
    feedback_dir = resolve_data_dir(base_dir) / "feedback"
    marker_path = feedback_dir / ".sqlite-feedback-migrated"
    stats = {"migrated": 0, "skipped": 0}
    if marker_path.exists():
        return stats

    legacy_path = feedback_dir / "events.jsonl"
    repository = UserActionRepository(base_dir)
    if legacy_path.exists():
        for line_no, raw_line in enumerate(legacy_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid legacy feedback JSONL at line {line_no}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"legacy feedback record must be an object at line {line_no}")
            _migrate_event(base_dir, repository, event)
            stats["migrated"] += 1
    feedback_dir.mkdir(parents=True, exist_ok=True)
    marker_path.touch()
    return stats


def _migrate_event(base_dir: Path | str, repository: UserActionRepository, event: dict[str, Any]) -> None:
    action_id = event.get("event_id")
    card_id = event.get("card_id")
    action = event.get("action")
    if not isinstance(action_id, str) or not action_id:
        raise ValueError("legacy feedback requires event_id")
    if not isinstance(card_id, str) or not card_id:
        raise ValueError("legacy feedback requires card_id")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"unsupported legacy feedback action: {action}")
    topic = event.get("topic")
    topic_text = topic if isinstance(topic, str) else None
    topic_id, topic_label = resolve_feedback_topic(base_dir, topic_text)
    details: dict[str, Any] = {"source_url": str(event.get("source_url") or "")}
    if isinstance(event.get("note"), str) and event["note"]:
        details["note"] = event["note"]
    if topic_label:
        details["topic_label"] = topic_label
    repository.upsert_action(
        {
            "action_id": action_id,
            "card_id": card_id,
            "topic_id": topic_id,
            "action": action,
            "created_at": event.get("created_at"),
            "details": details,
        }
    )
