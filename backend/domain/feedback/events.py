from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from dataclasses import asdict, dataclass
from typing import Any, Mapping
from uuid import uuid4


ALLOWED_ACTIONS = {
    "saved",
    "dismissed",
    "promoted_to_task",
    "added_to_topic",
    "used_in_draft",
    "marked_noise",
}




@dataclass(frozen=True)
class FeedbackEvent:
    event_id: str
    created_at: str
    card_id: str
    source_url: str
    action: str
    topic: str | None = None
    note: str | None = None

    @classmethod
    def create(
        cls,
        *,
        card_id: str,
        source_url: str,
        action: str,
        topic: str | None = None,
        note: str | None = None,
    ) -> "FeedbackEvent":
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"unsupported feedback action: {action}")
        if not card_id:
            raise ValueError("feedback requires card_id")
        return cls(
            event_id=f"feedback_{uuid4().hex[:12]}",
            created_at=utc_now_micro_z(),
            card_id=card_id,
            source_url=source_url,
            action=action,
            topic=topic,
            note=note,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _bounded_delta(value: int) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def summarize_adjustments(events: list[FeedbackEvent | Mapping[str, Any]]) -> dict[str, dict[str, dict[str, int | str]]]:
    """Summarize durable feedback without reading storage or increasing its weight.

    The result keeps card-scoped and topic-scoped evidence separate. Callers
    select the relevant scopes, then make one bounded adjustment round.
    """
    topic_counts: dict[str, dict[str, int]] = {}
    card_counts: dict[str, dict[str, int]] = {}
    for event in events:
        payload = _event_payload(event)
        card_id = payload["card_id"]
        topic = payload["topic"]
        if card_id:
            _count_action(card_counts.setdefault(card_id, _empty_counts()), payload["action"])
        if topic:
            _count_action(topic_counts.setdefault(topic, _empty_counts()), payload["action"])
    return {
        "topics": {scope: _adjustment_payload(counts) for scope, counts in topic_counts.items()},
        "cards": {scope: _adjustment_payload(counts) for scope, counts in card_counts.items()},
    }


def _event_payload(event: FeedbackEvent | Mapping[str, Any]) -> dict[str, str | None]:
    if isinstance(event, FeedbackEvent):
        return {"card_id": event.card_id, "topic": event.topic, "action": event.action}
    return {
        "card_id": str(event.get("card_id") or "") or None,
        "topic": str(event.get("topic") or event.get("topic_id") or "") or None,
        "action": str(event.get("action") or ""),
    }


def _empty_counts() -> dict[str, int]:
    return {"positive_relevance": 0, "negative_relevance": 0, "positive_creation": 0}


def _count_action(counts: dict[str, int], action: str) -> None:
    if action in {"saved", "promoted_to_task", "added_to_topic", "used_in_draft"}:
        counts["positive_relevance"] += 1
    if action in {"dismissed", "marked_noise"}:
        counts["negative_relevance"] += 1
    if action == "used_in_draft":
        counts["positive_creation"] += 1


def _adjustment_payload(counts: dict[str, int]) -> dict[str, int | str]:
    relevance_delta = _bounded_delta(counts["positive_relevance"] - counts["negative_relevance"])
    creation_delta = _bounded_delta(counts["positive_creation"])
    reasons = []
    if relevance_delta > 0:
        reasons.append("positive saved/promoted/added_to_topic/used_in_draft feedback")
    if relevance_delta < 0:
        reasons.append("dismissed or marked_noise feedback")
    if creation_delta > 0:
        reasons.append("used_in_draft feedback")
    return {
        "relevance_delta": relevance_delta,
        "creation_delta": creation_delta,
        "reason": "; ".join(reasons) or "no bounded adjustment",
    }
