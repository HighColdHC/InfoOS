"""The evaluation key retains its first write time across overwrites."""
import sqlite3

import pytest

from backend.application.usecases.interest_profiles import push_profile
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from tests.infoos.test_daily_intelligence import PROFILE, _card, _db


def test_overwrite_preserves_first_created_at_and_updates_payload(tmp_path):
    db = _db(tmp_path)
    push_profile(tmp_path, PROFILE, source="test")
    _card(db, 1, "2026-09-29T12:00:00Z")
    repo = CardEvaluationRepository(tmp_path)
    before = repo.latest_for_card("card-1")
    changes = {"model": "new-agent", "provider_id": "new-provider", "quality": 55,
               "relevance": 40, "attribution_kind": "domain", "attribution_ref": "new-ref",
               "content_type": "tool", "suggested_action": "aware", "paused_landmark": True,
               "summary": "new-summary", "relation_note": "new-relation", "passed_quality": False}
    with sqlite3.connect(db) as conn:
        assert repo.insert(conn, {**before, **changes, "evaluation_id": "ignored-id",
                                 "created_at": "2026-10-01T00:00:00.123456Z"}, overwrite=True)
    after = repo.latest_for_card("card-1")
    assert after["created_at"] == before["created_at"]
    assert after["evaluation_id"] == before["evaluation_id"]
    for field, expected in changes.items():
        assert after[field] == expected


@pytest.mark.parametrize("changes", [
    {"card_version": 2}, {"profile_version": 2}, {"prompt_version": "new-rubric"},
])
def test_new_evaluation_key_has_its_own_creation_time(tmp_path, changes):
    db = _db(tmp_path)
    push_profile(tmp_path, PROFILE, source="test")
    push_profile(tmp_path, PROFILE + "\n- second item", source="test")
    _card(db, 1, "2026-09-29T12:00:00Z")
    repo = CardEvaluationRepository(tmp_path)
    before = repo.latest_for_card("card-1")
    with sqlite3.connect(db) as conn:
        assert repo.insert(conn, {**before, **changes, "evaluation_id": "new-key",
                                 "created_at": "2026-10-01T00:00:00.123456Z"}, overwrite=True)
        rows = conn.execute("select created_at from card_evaluations order by created_at").fetchall()
    assert rows == [(before["created_at"],), ("2026-10-01T00:00:00.123456Z",)]
    assert repo.latest_for_card("card-1")["evaluation_id"] == "new-key"


def test_latest_new_key_overwrite_preserves_first_created_at(tmp_path):
    db = _db(tmp_path)
    push_profile(tmp_path, PROFILE, source="test")
    _card(db, 1, "2026-09-29T12:00:00Z")
    repo = CardEvaluationRepository(tmp_path)
    old = repo.latest_for_card("card-1")
    assert old["card_version"] == 1
    first_created_at = "2026-10-01T00:00:00.123456Z"
    new = {**old, "evaluation_id": "new-key", "card_version": 2,
           "summary": "new-key-summary", "created_at": first_created_at}
    changes = {"quality": 55, "relevance": 40, "summary": "rewritten-summary",
               "relation_note": "rewritten-relation", "passed_quality": False}
    with sqlite3.connect(db) as conn:
        assert repo.insert(conn, new)
        assert repo.insert(conn, {**new, **changes, "evaluation_id": "ignored-id",
                                 "created_at": "2026-10-02T00:00:00.123456Z"}, overwrite=True)
        assert conn.execute(
            "select count(*) from card_evaluations where card_id = ?", ("card-1",),
        ).fetchone()[0] == 2
    latest = repo.latest_for_card("card-1")
    assert latest["evaluation_id"] == "new-key"
    assert latest["card_version"] == 2
    assert latest["created_at"] == first_created_at
    for field, expected in changes.items():
        assert latest[field] == expected
