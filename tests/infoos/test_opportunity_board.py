"""Main reader projections, acquisition counters and browser mutation security."""
import json
import sqlite3
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.opportunity_follow_up import set_opportunity_follow_up
from backend.infrastructure.storage.database import default_database_path
from tests.infoos.test_opportunity_follow_up import prepared
from tests.infoos.test_verifications import save as verify


def test_board_detail_stats_and_cross_site_put(tmp_path, monkeypatch):
    prepared(tmp_path)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        board = client.get("/api/opportunities/board").json()
        assert board["columns"]["inbox"]["count"] == 1
        tile = board["columns"]["inbox"]["items"][0]
        assert tile["risk_flags"] == [] and tile["question_count"] == 1
        assert client.get("/api/opportunities/board?query=不匹配").json()["columns"]["inbox"]["count"] == 0
        assert client.get("/api/opportunities/board?inbox_offset=1").json()["columns"]["inbox"]["items"] == []
        path = "/api/opportunities/card-1/follow-up"
        assert client.put(path, json={"state": "following"}, headers={"Origin": "https://evil.test"}).status_code == 403
        first = client.put(path, json={"state": "following"}, headers={"Origin": "http://testserver"})
        assert first.status_code == 200 and first.json()["stage"] == "plan"
        assert client.put(path, json={"state": "following"}, headers={"Origin": "http://testserver"}).json() == first.json()
        assert client.put(path, json={"state": "action"}, headers={"Origin": "http://testserver"}).json()["code"] == "invalid_follow_up_state"
        verify(tmp_path, conclusion="keep_observing", doubts="仍需验证交付")
        detail = client.get("/api/opportunities/card-1").json()
        assert detail["stage"] == "contact"
        assert [entry["kind"] for entry in detail["timeline"]] == ["breakdown", "follow_up", "verification"]
        assert detail["labels"]["monetization_model"] == "课程/培训"
        stats = client.get("/api/opportunities/stats").json()
        assert stats["funnel"]["broken_down"] == stats["funnel"]["following"] == stats["funnel"]["contacted"] == 1
        assert stats["follow_up"]["stages"]["contact"] == 1
        assert stats["follow_up"]["median_contact_days"] >= 0
        assert stats["risks"][3]["count"] == 1
        assert client.get("/api/opportunities/missing").status_code == 404
        assert client.get("/api/opportunities/board?inbox_limit=0").status_code == 422
        assert client.get("/api/opportunities/stats?window=bad").status_code == 422
    set_opportunity_follow_up(tmp_path, "card-1", "following", actor="web")
    # A new decision after contact must append even if an older event had the same state.
    from backend.read_models.opportunity_board import board as read_board
    assert read_board(tmp_path)["columns"]["plan"]["count"] == 1
    set_opportunity_follow_up(tmp_path, "card-1", "reset", actor="web")
    assert read_board(tmp_path)["columns"]["contact"]["count"] == 1


def test_stats_historical_payload_and_shanghai_day(tmp_path, monkeypatch):
    prepared(tmp_path)
    from backend.read_models import opportunity_stats
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(opportunity_stats, "datetime", Clock)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        source = "stats-source"
        conn.execute("insert into sources(source_id,platform,source_type,identifier,display_name,cadence_hours,"
            "connector_status,created_at,updated_at) values(?, 'douyin','home','home','抖音',1,'ready',?,?)",
            (source, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"))
        for index, (stamp, payload) in enumerate([
            ("2026-10-14T15:59:59Z", {"prefilter_summary": {"checked": 999, "matched": 999}}),
            ("2026-10-15T16:00:00Z", {"prefilter_summary": {"checked": 20, "matched": 3, "unknown": 2}}),
            ("2026-10-21T18:00:00Z", {"prefilter_summary": {}}),
            ("2026-10-21T18:00:00Z", {}),
            ("2026-10-21T18:00:00Z", {"prefilter_summary": None}),
        ]):
            conn.execute("insert into run_history(run_id,source_id,scheduled_for,started_at,finished_at,status,"
                "visible_count,trigger,raw_payload) values(?,?,?,?,?,'success',30,'test',?)",
                (f"run-{index}", source, stamp, stamp, stamp, json.dumps(payload)))
    stats = opportunity_stats.stats(tmp_path, "7d")
    assert stats["funnel"]["checked"] == 20 and stats["funnel"]["matched"] == 5
    assert stats["funnel"]["visible"] == 60
    assert [day["date"] for day in stats["daily"]] == [f"2026-10-{n}" for n in range(16, 23)]


def test_board_sum_order_observe_filters_and_current_profile_candidates(tmp_path):
    from backend.read_models.opportunity_board import board
    from backend.read_models.opportunity_stats import stats
    from tests.infoos.test_daily_intelligence import _card
    from tests.infoos.test_opportunity_breakdowns import mark, save
    from tests.infoos.test_opportunity_domain import item
    prepared(tmp_path)
    for number, rep, cred, step in [(2, 90, 5, "verify"), (3, 70, 80, "verify"), (4, 50, 40, "observe")]:
        _card(default_database_path(tmp_path), number, f"2026-10-{number:02}T00:00:00Z", evaluated=False)
        mark(tmp_path, number)
        save(tmp_path, [item(f"card-{number}", replicability=rep, credibility=cred,
                             next_step=step, verify_questions=["交付是什么？"])])
    page = board(tmp_path, inbox_limit=2)
    assert [row["card_id"] for row in page["columns"]["inbox"]["items"]] == ["card-3", "card-1"]
    assert page["columns"]["inbox"]["count"] == 3
    assert board(tmp_path, inbox_offset=2)["columns"]["inbox"]["items"][0]["card_id"] == "card-2"
    assert board(tmp_path, include_observe=True)["columns"]["inbox"]["count"] == 4
    assert board(tmp_path, monetization_model="ecommerce")["columns"]["inbox"]["count"] == 0
    assert stats(tmp_path)["progress"]["pending"] == 0
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update raw_items set revision=revision+1 where raw_item_id='card-3'")
    assert stats(tmp_path)["progress"]["pending"] == 1
