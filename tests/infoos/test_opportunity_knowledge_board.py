"""Knowledge board sorting, filtering, Shanghai statistics and reader writes."""
from datetime import datetime
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.usecases.set_research_status import set_research_status
from backend.domain.intelligence.knowledge import RESEARCH_LABELS, RESEARCH_STATUSES
from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.infrastructure.storage.database import default_database_path
from backend.read_models.knowledge import get_knowledge_card
from backend.read_models.opportunity_board import board
from backend.read_models import opportunity_stats
from tests.infoos.test_knowledge_cards import library as library
from tests.infoos.test_knowledge_cards import seed, extract
from tests.infoos.test_knowledge_domain import knowledge, TEXT


@pytest.fixture
def opportunities(library):
    ids = []
    for number in range(1, 7):
        seed(library, number, extract_kinds=["opportunity", "project"])
        value = knowledge("opportunity" if number < 6 else "project", title=f"课程 {number}",
                          strength=60 if number == 1 else 80)
        if number == 2:
            value["fields"]["money_signal"] = "培训成交"
            value["fields"]["signal_kinds"] = ["sales", "sales", "price"]
        if number == 3:
            value["subtype"] = "selling"
        result = extract(library, f"card-{number}", [value])
        assert result["status"] == "accepted", result
        ids.append(result["cards"][0]["knowledge_id"])
    seed(library, 7)
    result = extract(library, "card-7", [{"action": "merge", "kind": "opportunity", "target_knowledge_id": ids[1],
                                       "quotes": [TEXT], "note": None}])
    assert result["status"] == "accepted", result
    with sqlite3.connect(default_database_path(library)) as conn:
        for kid, stamp in zip(ids, ["2026-10-27T00:00:00.000000Z", "2026-10-27T15:59:59.000000Z",
                                  "2026-10-27T16:00:00.000000Z", "2026-10-01T00:00:00.000000Z",
                                  "2026-10-27T00:00:00.000000Z", "2026-10-27T00:00:00.000000Z"]):
            conn.execute("update knowledge_cards set created_at=? where knowledge_id=?", (stamp, kid))
        conn.execute("update knowledge_cards set status='superseded',superseded_by=? where knowledge_id=?", (ids[0], ids[4]))
        conn.execute("update cards set source_platform='rss'")
        conn.execute("update cards set source_platform='github' where card_id in ('card-2','card-7')")
    set_research_status(library, ids[3], "observe", None, actor="test")
    return library, ids


def change(base, kid, status, stamp, monkeypatch):
    monkeypatch.setattr("backend.application.usecases.set_research_status.utc_now_micro_z", lambda: stamp)
    return set_research_status(base, kid, status, None, actor="test")


def test_board_columns_order_and_item_fields(opportunities, monkeypatch):
    base, ids = opportunities
    columns = board(base)["columns"]
    assert [row["status"] for row in columns] == list(RESEARCH_STATUSES)
    assert [row["label"] for row in columns] == list(RESEARCH_LABELS.values())
    assert [row["total"] for row in columns] == [3, 0, 0, 1, 0]
    assert [row["knowledge_id"] for row in columns[0]["items"]] == [ids[2], ids[1], ids[0]]
    row = columns[0]["items"][1]
    assert row["status_changed_at"] == row["created_at"] == "2026-10-27T15:59:59.000000Z"
    assert row["evidence_count"] == 2 and row["platforms"] == ["github"]
    assert row["money_signal"] == "培训成交" and row["signal_kinds"] == ["sales", "sales", "price"]
    with sqlite3.connect(default_database_path(base)) as conn:
        conn.execute("update cards set source_platform='rss' where card_id='card-7'")
    assert board(base)["columns"][0]["items"][1]["platforms"] == ["github", "rss"]
    change(base, ids[0], "observe", "2026-10-28T01:00:00.000000Z", monkeypatch)
    change(base, ids[3], "researching", "2026-10-28T02:00:00.000000Z", monkeypatch)
    change(base, ids[3], "observe", "2026-10-28T03:00:00.000000Z", monkeypatch)
    observed = board(base)["columns"][3]["items"]
    assert [row["knowledge_id"] for row in observed] == [ids[3], ids[0]]
    assert observed[0]["status_changed_at"] == "2026-10-28T03:00:00.000000Z"
    assert {row["knowledge_id"] for col in board(base)["columns"] for row in col["items"]} == set(ids[:4])


def test_board_filters_and_pagination(opportunities):
    base, ids = opportunities
    matched = board(base, query="  课程   培训成交 ")["columns"][0]
    assert matched["total"] == 1 and matched["items"][0]["knowledge_id"] == ids[1]
    assert all(col["total"] == 0 for col in board(base, query="课程 missing")["columns"])
    assert board(base, subtype="selling")["columns"][0]["items"][0]["knowledge_id"] == ids[2]
    selected = board(base, platform="github")["columns"][0]
    assert selected["total"] == len(selected["items"]) == 1
    first = board(base, status="pending", limit=2)["columns"]
    assert len(first) == 1 and first[0]["next_offset"] == 2
    second = board(base, status="pending", limit=2, offset=2)["columns"][0]
    assert second["next_offset"] is None and second["items"][0]["knowledge_id"] == ids[0]
    for kwargs, code in [({"status": "bad"}, "invalid_research_status"), ({"subtype": "bad"}, "invalid_subtype")]:
        with pytest.raises(ValueError, match=code):
            board(base, **kwargs)


def test_stats_blocks_and_shanghai_window(opportunities, monkeypatch):
    base, _ = opportunities
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 4, tzinfo=tz)
    monkeypatch.setattr(opportunity_stats, "datetime", Clock)
    stats = opportunity_stats.stats(base, "7d")
    assert stats["total"] == 3
    assert [row["status"] for row in stats["by_status"]] == list(RESEARCH_STATUSES)
    assert [row["count"] for row in stats["by_status"]] == [3, 0, 0, 0, 0]
    assert [row["subtype"] for row in stats["by_subtype"]] == list(KINDS["opportunity"].subtypes)
    assert [row["count"] for row in stats["by_subtype"]] == [2, 0, 1, 0]
    assert stats["by_signal"] == [{"signal": key, "count": count} for key, count in
                                    [("revenue", 2), ("price", 1), ("sales", 1), ("orders", 0), ("deals", 0), ("ads", 0)]]
    assert [(row["platform"], row["count"]) for row in stats["by_platform"]] == [("rss", 2), ("github", 1)]
    assert len(stats["daily"]) == 7 and stats["daily"][0] == {"date": "2026-10-22", "count": 0}
    assert stats["daily"][-2:] == [{"date": "2026-10-27", "count": 2}, {"date": "2026-10-28", "count": 1}]
    all_stats = opportunity_stats.stats(base)
    assert all_stats["total"] == 4 and all_stats["daily"][0] == {"date": "2026-10-01", "count": 1}
    assert all_stats["by_status"][3]["count"] == 1


def test_api_board_stats_and_research_status_put(opportunities):
    base, ids = opportunities
    with TestClient(create_app(base)) as client:
        assert client.get("/api/opportunities/board").json() == board(base)
        assert client.head("/api/opportunities/board").status_code == 200
        assert client.get("/api/opportunities/stats").json()["total"] == 4
        path = f"/api/opportunities/{ids[0]}/research-status"
        assert client.put(path, json={"status": "researching"}, headers={"Origin": "https://evil.test"}).status_code == 403
        result = client.put(path, json={"status": "researching", "note": "准备调研"}, headers={"Origin": "http://testserver"})
        assert result.status_code == 200 and result.json()["changed"] is True
        assert client.put(path, json={"status": "researching", "note": None}).json()["changed"] is False
        history = get_knowledge_card(base, ids[0])["status_history"]
        assert len(history) == 1 and history[0]["actor"] == "web" and history[0]["note"] == "准备调研"
        for payload, code in [({"status": "bad"}, "invalid_research_status"),
                              ({"status": "observe", "note": "字" * 201}, "invalid_note"),
                              ({"status": 1}, "invalid_request")]:
            response = client.put(path, json=payload)
            assert response.status_code == 422 and response.json()["code"] == code
        for kid, status, code in [(ids[5], 422, "not_opportunity_card"), ("missing", 404, "knowledge_not_found")]:
            response = client.put(f"/api/opportunities/{kid}/research-status", json={"status": "observe"})
            assert response.status_code == status and response.json()["code"] == code
        for params, code in [("limit=0", "invalid_pagination"), ("offset=-1", "invalid_pagination"),
                             ("status=bad", "invalid_research_status"), ("subtype=bad", "invalid_subtype")]:
            response = client.get("/api/opportunities/board?" + params)
            assert response.status_code == 422 and response.json()["code"] == code
        assert client.get("/api/opportunities/stats?window=bad").status_code == 422
