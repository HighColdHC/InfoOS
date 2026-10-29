from __future__ import annotations

import json

from backend.application.workflows.migrate_feedback import migrate_legacy_feedback
from backend.infrastructure.storage.repositories import CardRepository, TopicRepository, UserActionRepository


def _seed_card(base_dir, card_id: str) -> None:
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "source_platform": "rss",
            "source_type": "article",
            "source_url": f"https://example.com/{card_id}",
            "title": card_id,
            "content": "content",
            "captured_at": "2026-07-10T00:00:00Z",
            "processed_at": "2026-07-10T00:00:00Z",
        }
    )


def test_user_action_round_trip_in_sqlite(tmp_path):
    repository = UserActionRepository(tmp_path)
    _seed_card(tmp_path, "card_1")
    TopicRepository(tmp_path).upsert_topic({"topic_id": "topic_1", "title": "Topic 1"})
    stored = repository.upsert_action(
        {
            "action_id": "action_1",
            "card_id": "card_1",
            "topic_id": "topic_1",
            "decision_id": "decision_1",
            "artifact_id": "artifact_1",
            "action": "saved",
            "status": "recorded",
            "details": {"note": "keep", "source_url": "https://example.com"},
        }
    )

    assert stored["details"] == {"note": "keep", "source_url": "https://example.com"}
    assert repository.get("action_1")["decision_id"] == "decision_1"
    assert repository.list_actions(card_id="card_1")[0]["topic_id"] == "topic_1"
    assert repository.list_actions(topic_id="topic_1")[0]["artifact_id"] == "artifact_1"
    assert repository.list_actions(decision_id="decision_1")[0]["action_id"] == "action_1"
    assert repository.delete("action_1") is True
    assert repository.get("action_1") is None


def test_legacy_feedback_jsonl_migrates_once(tmp_path):
    legacy_path = tmp_path / "data" / "feedback" / "events.jsonl"
    legacy_path.parent.mkdir(parents=True)
    _seed_card(tmp_path, "card_legacy")
    legacy_path.write_text(
        json.dumps(
            {
                "event_id": "feedback_legacy",
                "created_at": "2026-07-10T00:00:00Z",
                "card_id": "card_legacy",
                "source_url": "https://example.com/legacy",
                "action": "saved",
                "topic": "Unmapped topic",
                "note": "migrate me",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = migrate_legacy_feedback(tmp_path)

    action = UserActionRepository(tmp_path).get("feedback_legacy")
    assert result == {"migrated": 1, "skipped": 0}
    assert action["card_id"] == "card_legacy"
    assert action["topic_id"] is None
    assert action["details"] == {
        "note": "migrate me",
        "source_url": "https://example.com/legacy",
        "topic_label": "Unmapped topic",
    }
    assert (tmp_path / "data" / "feedback" / ".sqlite-feedback-migrated").exists()


def test_feedback_migration_is_idempotent(tmp_path):
    legacy_path = tmp_path / "data" / "feedback" / "events.jsonl"
    legacy_path.parent.mkdir(parents=True)
    _seed_card(tmp_path, "card_once")
    legacy_path.write_text(
        '{"event_id":"feedback_once","created_at":"2026-07-10T00:00:00Z","card_id":"card_once","source_url":"","action":"saved"}\n',
        encoding="utf-8",
    )

    assert migrate_legacy_feedback(tmp_path) == {"migrated": 1, "skipped": 0}
    assert migrate_legacy_feedback(tmp_path) == {"migrated": 0, "skipped": 0}
    assert len(UserActionRepository(tmp_path).list_actions(card_id="card_once")) == 1
