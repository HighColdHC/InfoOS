from __future__ import annotations

import sqlite3

import pytest
from pathlib import Path

from backend.application.tasks.daily_brief_planner import enqueue_daily_brief_build_task
from backend.application.tasks.repository import TaskRepository
from backend.application.usecases.daily_brief import brief_window, build_daily_brief
from backend.application.usecases.interest_profiles import push_profile
from backend.infrastructure.storage.card_entry import first_entered_at
from backend.infrastructure.storage.daily_briefs import DailyBriefRepository
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.migrations.runner import migrate_database
from backend.read_models.daily_brief import updates_since

PROFILE = "\n".join(f"## {name}\n- item" for name in (
    "主线与KR", "进行中项目", "正在追问的问题", "暂放方向", "长期领域"))


def _db(base: Path) -> Path:
    db = default_database_path(base)
    migrate_database(db)
    return db


def _card(db: Path, number: int, entered: str | None, kind: str = "project",
          quality: int = 80, relevance: int = 80, passed: int = 1,
          landmark: int = 0, evaluated: bool = True, content_type: str = "method") -> None:
    card_id = f"card-{number}"
    with sqlite3.connect(db) as conn:
        if entered is not None:
            conn.execute(
                """insert into raw_items (connector_id, raw_item_id, source_platform, source_type,
                   source_url, title, captured_at, raw_payload, first_seen_at, last_seen_at)
                   values ('rss', ?, 'rss', 'article', ?, ?, 'bad timestamp', '{}', ?, ?)""",
                (card_id, f"https://example.test/{number}", card_id, entered, entered),
            )
        conn.execute(
            """insert into cards (card_id, version, updated_at, status, completeness_status,
               missing_reasons, raw_connector_id, raw_item_id, source_platform, source_type,
               source_url, source_author, title, content, captured_at, processed_at)
               values (?, 1, '', 'active', 'complete', '[]', 'rss', ?, 'rss', 'article',
                       ?, 'author', ?, 'body', 'non ISO', '2026-09-30T02:00:00.000000Z')""",
            (card_id, card_id, f"https://example.test/{number}", card_id),
        )
        if evaluated:
            conn.execute(
                """insert into card_evaluations
                  (evaluation_id, card_id, card_version, profile_version, prompt_version,
                   model, provider_id, quality, relevance, attribution_kind, attribution_ref,
                   content_type, suggested_action, paused_landmark, summary, relation_note,
                   passed_quality, created_at)
                   values (?, ?, 1, 1, 'evaluate_v1', 'model', 'provider', ?, ?, ?, 'item',
                           ?, 'read', ?, 'summary', 'relation', ?, '2026-09-30T02:00:00.000000Z')""",
                (f"eval-{number}", card_id, quality, relevance, kind, content_type, landmark, passed),
            )


def test_new_brief_without_extractions_and_profile(tmp_path):
    db = _db(tmp_path)
    assert build_daily_brief(tmp_path, "2026-09-29") == {"status": "skipped", "reason": "profile_missing"}
    push_profile(tmp_path, PROFILE, source="test")
    _card(db, 1, "2026-09-28T12:00:00Z")
    brief = build_daily_brief(tmp_path, "2026-09-29")["brief"]
    assert brief["layout"] == "knowledge_v1" and brief["items"] == []
    assert brief["weekly_paused"] is None
    assert brief_window("2026-09-29") == ("2026-09-27T23:00:00Z", "2026-09-28T23:00:00Z")
    assert first_entered_at(tmp_path, "card-1") == "2026-09-28T12:00:00Z"
    assert build_daily_brief(tmp_path, "2026-09-29")["status"] == "frozen"
    assert build_daily_brief(tmp_path, "2026-09-29", force=True)["status"] == "rebuilt"


@pytest.mark.parametrize("quality,content_type,passed,selected", [
    (79, "case", 1, False),
    (80, "case", 1, True),
    (80, "method", 1, True),
    (80, "tool", 1, True),
    (80, "data", 1, True),
    (100, "event", 1, False),
    (100, "opinion", 1, False),
    (100, "case", 0, False),
])
def test_exploration_thresholds_apply_to_brief_and_updates(tmp_path, quality, content_type, passed, selected):
    db = _db(tmp_path)
    push_profile(tmp_path, PROFILE, source="test")
    _card(db, 1, "2026-09-28T12:00:00Z", "exploration", quality=quality,
          content_type=content_type, passed=passed)
    _card(db, 2, "2026-09-28T11:00:00Z", quality=60, content_type="event")
    expected = ["card-1"] if selected else []
    updates = updates_since(tmp_path, "2026-09-28T10:00:00Z", limit=1)
    assert [i["card_id"] for i in updates["exploration"]] == expected
    assert [i["card_id"] for i in updates["related"]] == ([] if selected else ["card-2"])


def test_scheduler_one_task_per_shanghai_day(tmp_path):
    _db(tmp_path)
    before = "2026-09-28T22:59:59Z"
    after = "2026-09-28T23:00:00Z"
    assert enqueue_daily_brief_build_task(tmp_path, now=before) is None
    first = enqueue_daily_brief_build_task(tmp_path, now=after)
    assert first == "daily_brief_build_2026-09-29"
    assert enqueue_daily_brief_build_task(tmp_path, now="2026-09-29T12:00:00Z") == first
    tasks = TaskRepository.for_base_dir(tmp_path).list_pending()
    assert [task.task_id for task in tasks] == [first]
