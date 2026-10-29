from __future__ import annotations

import sqlite3

import pytest

from backend.application.usecases.interest_profiles import push_profile
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.migrations.runner import migrate_database


def test_evaluation_and_brief_uniqueness(tmp_path):
    path = default_database_path(tmp_path)
    migrate_database(path)
    profile = "\n".join(f"## {name}\n- item" for name in (
        "主线与KR", "进行中项目", "正在追问的问题", "暂放方向", "长期领域",
    ))
    push_profile(tmp_path, profile, source="mcp:test")
    with sqlite3.connect(path) as conn:
        conn.execute("pragma foreign_keys = on")
        conn.execute("""insert into cards
          (card_id, card_type, version, updated_at, status, completeness_status,
           missing_reasons, source_platform, source_type, source_url, title, content,
           captured_at, processed_at, workflow_status, ignored, skimmed, raw_payload)
          values ('card-1','information',1,'2026-01-01T00:00:00Z','active','complete',
                  '[]','rss','article','https://example.test','Title','Body',
                  '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z','new',0,0,'{}')""")
        evaluation = {
            "evaluation_id": "eval-1", "card_id": "card-1", "card_version": 1,
            "profile_version": 1, "prompt_version": "p1", "model": "test-model",
            "provider_id": "test-provider", "quality": 90, "relevance": 80,
            "attribution_kind": "project", "attribution_ref": "item",
            "content_type": "method", "suggested_action": "read",
            "paused_landmark": False, "summary": "summary", "relation_note": "relation",
            "passed_quality": True, "receipt_id": None,
            "created_at": "2026-09-30T02:00:00Z",
        }
        assert CardEvaluationRepository.insert(conn, evaluation)
        assert not CardEvaluationRepository.insert(conn, {**evaluation, "evaluation_id": "eval-2"})
        with pytest.raises(sqlite3.IntegrityError, match="quality"):
            CardEvaluationRepository.insert(conn, {**evaluation, "evaluation_id": "eval-3", "prompt_version": "p2", "quality": 101})
        brief = {
            "brief_date": "2026-09-29", "window_start": "2026-09-27T23:00:00Z",
            "window_end": "2026-09-28T23:00:00Z", "profile_version": 1,
            "items": [{"number": 1, "card_id": "card-1"}],
        }
        assert DailyBriefRepository.insert(conn, brief)
        assert not DailyBriefRepository.insert(conn, brief)
    latest = CardEvaluationRepository(tmp_path).latest_for_card("card-1")
    assert latest["evaluation_id"] == "eval-1"
    assert latest["created_at"] == "2026-09-30T02:00:00.000000Z"
    assert len(CardEvaluationRepository(tmp_path).in_window("2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z")) == 1
    assert DailyBriefRepository(tmp_path).get("2026-09-29")["items"][0]["number"] == 1
