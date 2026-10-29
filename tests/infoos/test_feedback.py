import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.feedback.events import FeedbackEvent, summarize_adjustments


def test_feedback_rejects_invalid_action():
    with pytest.raises(ValueError, match="unsupported feedback action"):
        FeedbackEvent.create(card_id="card_1", source_url="https://example.com/1", action="liked")


def test_feedback_adjustments_are_bounded_and_explainable():
    events = [
        FeedbackEvent.create(card_id="card_1", source_url="https://example.com/1", action="used_in_draft", topic="InfoOS"),
        FeedbackEvent.create(card_id="card_2", source_url="https://example.com/2", action="added_to_topic", topic="InfoOS"),
        FeedbackEvent.create(card_id="card_3", source_url="https://example.com/3", action="marked_noise", topic="Viral"),
        FeedbackEvent.create(card_id="card_4", source_url="https://example.com/4", action="dismissed", topic="Viral"),
    ]

    summary = summarize_adjustments(events)

    assert summary["topics"]["InfoOS"]["relevance_delta"] == 1
    assert summary["topics"]["InfoOS"]["creation_delta"] == 1
    assert "used_in_draft" in summary["topics"]["InfoOS"]["reason"]
    assert summary["topics"]["Viral"]["relevance_delta"] == -1
    assert summary["topics"]["Viral"]["creation_delta"] == 0


def test_feedback_adjustments_accept_durable_action_payloads():
    summary = summarize_adjustments(
        [
            {
                "action_id": "action_saved",
                "card_id": "card_1",
                "action": "saved",
                "topic_id": "topic_1",
                "updated_at": "2026-07-10T00:00:00Z",
            }
        ]
    )

    assert summary["topics"]["topic_1"]["relevance_delta"] == 1
