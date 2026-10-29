"""Case candidates use the latest general evaluation and reach the board."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.storage.database import default_database_path
from backend.read_models.opportunities import list_opportunities, list_opportunity_candidates
from tests.infoos.test_agent_evaluations import seed
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_opportunity_breakdowns import START, mark, save
from tests.infoos.test_opportunity_domain import item


def case_card(base, number=2, **changes):
    _card(default_database_path(base), number, START, content_type="case", relevance=60, **changes)


def test_case_candidate_submission_current_breakdown_and_read_models(tmp_path):
    seed(tmp_path)
    case_card(tmp_path)
    page = list_opportunity_candidates(tmp_path, START)
    assert [(row["card_id"], row["candidate_kind"]) for row in page["items"]] == [("card-2", "case")]
    result = save(tmp_path, [item("card-2", next_step="verify", verify_questions=["如何复刻获客路径？"])])[0]
    assert result["status"] == "accepted"
    assert list_opportunity_candidates(tmp_path, START)["items"] == []
    row = list_opportunity_candidates(tmp_path, START, only_unbroken=False)["items"][0]
    assert row["candidate_kind"] == "case" and row["has_current_breakdown"]
    assert list_opportunities(tmp_path)["items"][0]["candidate_kind"] == "case"
    with TestClient(create_app(tmp_path)) as client:
        tile = client.get("/api/opportunities/board").json()["columns"]["inbox"]["items"][0]
        assert tile["candidate_kind"] == "case"
        assert client.get("/api/opportunities/card-2").json()["candidate_kind"] == "case"
        assert client.get("/api/opportunities/board?candidate_kind=case").json()["columns"]["inbox"]["count"] == 1
        assert client.get("/api/opportunities/board?candidate_kind=prefilter").json()["columns"]["inbox"]["count"] == 0


@pytest.mark.parametrize("changes", [{"passed": 0}, {"relevance": 59}, {"content_type": "method"}])
def test_ineligible_evaluation_is_not_case_candidate(tmp_path, changes):
    seed(tmp_path)
    _card(default_database_path(tmp_path), 2, START, **{"content_type": "case", "relevance": 60, **changes})
    assert list_opportunity_candidates(tmp_path, START)["items"] == []


@pytest.mark.parametrize("same_time", [False, True])
def test_only_latest_evaluation_controls_case_candidate(tmp_path, same_time):
    seed(tmp_path)
    case_card(tmp_path)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("""insert into card_evaluations
            (evaluation_id,card_id,card_version,profile_version,prompt_version,model,provider_id,
             quality,relevance,content_type,suggested_action,paused_landmark,summary,relation_note,passed_quality,created_at)
            select 'eval-z','card-2',2,profile_version,prompt_version,model,provider_id,
                quality,relevance,'method',suggested_action,paused_landmark,summary,relation_note,passed_quality,?
            from card_evaluations where evaluation_id='eval-2'""",
            ("2026-09-30T02:00:00.000000Z" if same_time else "2026-10-01T00:00:00.000000Z",))
    assert list_opportunity_candidates(tmp_path, START)["items"] == []
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update card_evaluations set content_type='case' where evaluation_id='eval-z'")
        conn.execute("update card_evaluations set content_type='method' where evaluation_id='eval-2'")
    assert list_opportunity_candidates(tmp_path, START)["items"][0]["candidate_kind"] == "case"


def test_candidate_kind_priority(tmp_path):
    seed(tmp_path)
    case_card(tmp_path)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("""insert into sources(source_id,platform,source_type,identifier,display_name,cadence_hours,
            connector_status,created_at,updated_at) values ('wind','zsxq','opportunity','wind','风向标',24,
            'supported',?,?)""", (START, START))
        conn.execute("update raw_items set source_id='wind' where raw_item_id='card-2'")
    assert list_opportunity_candidates(tmp_path, START)["items"][0]["candidate_kind"] == "wind_vane"
    mark(tmp_path, 2)
    assert list_opportunity_candidates(tmp_path, START)["items"][0]["candidate_kind"] == "prefilter"
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update raw_items set raw_payload=? where raw_item_id='card-2'",
                     (json.dumps({"raw_metadata": {"tags": ["AI 风向标"]}}),))
        conn.execute("update sources set source_type='topics' where source_id='wind'")
    assert list_opportunity_candidates(tmp_path, START)["items"][0]["candidate_kind"] == "wind_vane"
