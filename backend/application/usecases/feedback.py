"""Use cases for feedback recorded as durable SQLite user actions."""

from __future__ import annotations

from pathlib import Path

from backend.infrastructure.storage.repositories import TopicRepository


def resolve_feedback_topic(base_dir: Path | str, topic: str | None) -> tuple[str | None, str | None]:
    """Resolve an API topic label to a real topic id without fabricating an FK."""
    if not topic:
        return None, None
    normalized = topic.strip()
    if not normalized:
        return None, None
    for candidate in TopicRepository(base_dir).list_topics():
        if normalized in {str(candidate["topic_id"]), str(candidate["title"])}:
            return str(candidate["topic_id"]), None
    return None, normalized
