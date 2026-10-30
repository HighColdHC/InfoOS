"""Candidate selection, extraction transactions and library projections."""
import json
import sqlite3

import pytest

from backend.application.usecases.interest_profiles import push_profile
from backend.application.usecases.set_research_status import set_research_status
from backend.application.usecases.submit_evaluations import submit_evaluations
from backend.application.usecases.submit_knowledge_extractions import submit_knowledge_extractions
from backend.infrastructure.storage.database import default_database_path
from backend.read_models.knowledge import get_knowledge_card, list_extraction_candidates, list_knowledge_cards, search_knowledge_cards
from tests.infoos.test_agent_evaluations import PROFILE, item
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_knowledge_domain import TEXT, knowledge

NOW = "2026-10-28T00:00:00.000000Z"
SINCE = "2026-10-27T00:00:00Z"


@pytest.fixture
def library(tmp_path, monkeypatch):
    for module in ("backend.application.usecases.submit_knowledge_extractions", "backend.application.usecases.submit_evaluations",
                   "backend.infrastructure.storage.knowledge_queries", "backend.application.usecases.set_research_status"):
        monkeypatch.setattr(module + ".utc_now_micro_z", lambda: NOW)
    push_profile(tmp_path, PROFILE, source="test")
    return tmp_path


def seed(base, number, *, channel=None, extract_kinds=None, exclusion=None, published=SINCE):
    db = default_database_path(base)
    _card(db, number, SINCE, evaluated=False)
    with sqlite3.connect(db) as conn:
        conn.execute("update cards set content=?,published_at=?,content_hash=? where card_id=?",
                     (TEXT, published, "sha256:" + "a" * 64, f"card-{number}"))
        if channel is not None:
            conn.execute("update cards set source_platform='zsxq',raw_payload=? where card_id=?",
                         (json.dumps({"is_digest": channel == "digest"}), f"card-{number}"))
            conn.execute("update raw_items set source_type=?,raw_payload=? where raw_item_id=?",
                         ("opportunity" if channel == "wind_vane" else "topics", json.dumps({}), f"card-{number}"))
    result = submit_evaluations(base, "test", [item(f"card-{number}", exclusion=exclusion,
        extract_kinds=extract_kinds if extract_kinds is not None else ["project"])], token_id="test")["results"][0]
    assert result["status"] == "accepted", result
    return f"card-{number}"


def extract(base, card_id, cards=None, **changes):
    value = {"card_id": card_id, "profile_version": 1, "outcome": "extracted", "cards": cards or [knowledge()], **changes}
    return submit_knowledge_extractions(base, "test", [value], token_id="test")["results"][0]


def test_candidates_exclusions_disabled_versions_cursors_and_nothing(library):
    base = library
    seed(base, 1, channel="digest", exclusion="ad", extract_kinds=[])
    seed(base, 2, exclusion="ad", extract_kinds=[])
    seed(base, 3, extract_kinds=["product", "resource"])
    seed(base, 4)
    seed(base, 5, channel="wind_vane", exclusion="entertainment", extract_kinds=[])
    page = list_extraction_candidates(base, SINCE, limit=1, cursor="")
    assert page["items"][0]["card_id"] == "card-1"
    assert page["items"][0]["deterministic_channel"] == "digest"
    second = list_extraction_candidates(base, SINCE, limit=1, cursor=page["next_cursor"])
    assert second["items"][0]["card_id"] == "card-4"
    assert [row["card_id"] for row in list_extraction_candidates(base, SINCE)["items"]] == ["card-1", "card-4", "card-5"]
    assert extract(base, "card-1", outcome="nothing", nothing_reason="  缺乏具体结果  ")["status"] == "accepted"
    assert [row["card_id"] for row in list_extraction_candidates(base, SINCE)["items"]] == ["card-4", "card-5"]
    with sqlite3.connect(default_database_path(base)) as conn:
        conn.execute("update cards set version=version+1 where card_id='card-1'")
    assert list_extraction_candidates(base, SINCE)["items"][0]["card_id"] == "card-1"
    assert extract(base, "card-2")["reason"] == "not_extraction_candidate"
    assert extract(base, "card-4", profile_version=2)["reason"] == "profile_version_mismatch"
    push_profile(base, PROFILE + "\n新画像", source="test")
    with pytest.raises(ValueError, match="invalid_cursor"):
        list_extraction_candidates(base, SINCE, cursor=page["next_cursor"])
    for stamp in ("2026-10-27", "2026-10-27T00:00:00"):
        with pytest.raises(ValueError, match="invalid_timestamp"):
            list_extraction_candidates(base, stamp)


def test_four_actions_quotes_conflicts_status_and_shared_origin(library):
    base = library
    for n in range(1, 6):
        seed(base, n, published="2026-10-20T00:00:00Z" if n == 2 else SINCE)
    result = extract(base, "card-1", [knowledge(), knowledge("opportunity")])
    assert result["status"] == "accepted", result
    target, opportunity = [row["knowledge_id"] for row in result["cards"]]
    assert get_knowledge_card(base, opportunity)["research_status"] == "pending"
    assert get_knowledge_card(base, target)["related_cards"][0]["knowledge_id"] == opportunity
    first = get_knowledge_card(base, target)
    for number, action in ((2, "merge"), (3, "update"), (4, "conflict")):
        value = {"action": action, "kind": "project", "target_knowledge_id": target, "quotes": [TEXT], "note": "新的结果证据"}
        if action == "update":
            value = knowledge(action=action, target_knowledge_id=target, strength=95, note="结果更新")
            value["fields"]["result"] = "已更新"
        assert extract(base, f"card-{number}", [value])["status"] == "accepted"
    detail = get_knowledge_card(base, target)
    assert {row["action"] for row in detail["evidence"]} == {"new", "merge", "update", "conflict"}
    assert detail["fields"]["result"] == "已更新" and detail["strength"] == 95 and detail["has_conflict"]
    assert detail["origin_freshness"] == first["origin_freshness"]
    assert detail["origin_published_at"] == "2026-10-20T00:00:00Z"
    assert len(search_knowledge_cards(base, "project", "案例 已更新")["items"]) == 1
    assert search_knowledge_cards(base, "project", "案例 missing")["items"] == []
    assert list_knowledge_cards(base, research_status="pending")["items"][0]["knowledge_id"] == opportunity
    assert set_research_status(base, opportunity, "observe", "用户决定", actor="test")["changed"]
    assert not set_research_status(base, opportunity, "observe", "重复", actor="test")["changed"]
    assert len(get_knowledge_card(base, opportunity)["status_history"]) == 1
    with pytest.raises(ValueError, match="not_opportunity_card"):
        set_research_status(base, target, "observe", None, actor="test")
    assert extract(base, "card-5", [knowledge(quotes=["伪造"])])["reason"] == "quote_not_in_card"
    missing_fields = knowledge()
    missing_fields.pop("fields")
    assert extract(base, "card-5", [missing_fields])["reason"] == "invalid_fields"
    assert extract(base, "card-5", [knowledge(action="update", target_knowledge_id=opportunity, note="更新")])["reason"] == "invalid_merge_target"


def test_per_item_transaction_rollback_and_fresh_news(library, monkeypatch):
    base = library
    seed(base, 1)
    seed(base, 2)
    seed(base, 3, published="2026-08-01T00:00:00Z")
    import backend.application.usecases.submit_knowledge_extractions as module
    original = module.KnowledgeCardRepository.write
    def fail_second(conn, value, **kwargs):
        if value["kind"] == "method":
            raise ValueError("injected_write_failure")
        return original(conn, value, **kwargs)
    monkeypatch.setattr(module.KnowledgeCardRepository, "write", fail_second)
    results = submit_knowledge_extractions(base, "test", [
        {"card_id": "card-1", "profile_version": 1, "outcome": "extracted", "cards": [knowledge(), knowledge("method")]},
        {"card_id": "card-2", "profile_version": 1, "outcome": "extracted", "cards": [knowledge()]},
    ], token_id="test")["results"]
    assert [row["status"] for row in results] == ["rejected", "accepted"]
    with sqlite3.connect(default_database_path(base)) as conn:
        assert conn.execute("select count(*) from knowledge_extractions").fetchone()[0] == 1
        assert conn.execute("select count(*) from knowledge_cards").fetchone()[0] == 1
        assert conn.execute("select count(*) from knowledge_evidence").fetchone()[0] == 1
        assert conn.execute("select card_id from knowledge_evidence").fetchone()[0] == "card-2"
    assert extract(base, "card-3", [knowledge("news")])["reason"] == "news_not_fresh"
    assert extract(base, "card-1", [knowledge("resource")])["reason"] == "kind_disabled"


def test_quote_rejection_detail_and_corrected_resubmission(library):
    quotes = [TEXT, "伪造"]
    reason = "quote_not_in_card"
    detail = "quotes[1]: not found in the source card or content blocks; copy a verbatim substring"
    seed(library, 1)
    seed(library, 2)
    results = submit_knowledge_extractions(library, "test", [
        {"card_id": "card-1", "profile_version": 1, "outcome": "extracted",
         "cards": [knowledge(), knowledge("method", quotes=quotes)]},
        {"card_id": "card-2", "profile_version": 1, "outcome": "extracted", "cards": [knowledge()]},
    ], token_id="test")["results"]
    assert results[0] == {"card_id": "card-1", "status": "rejected", "reason": reason,
                          "detail": f"cards[1].{detail}"}
    assert results[1]["status"] == "accepted"
    assert "detail" not in results[1]
    assert [row["card_id"] for row in list_extraction_candidates(library, SINCE)["items"]] == ["card-1"]
    corrected = extract(library, "card-1", [knowledge(), knowledge("method")])
    assert corrected["status"] == "accepted", corrected
    with sqlite3.connect(default_database_path(library)) as conn:
        assert conn.execute("select count(*) from knowledge_extractions").fetchone()[0] == 2
        assert conn.execute("select count(*) from knowledge_cards").fetchone()[0] == 3
