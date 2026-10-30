"""All-opportunity reader access, filters and preview breakdown markers."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.infrastructure.storage.database import default_database_path
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_opportunity_breakdowns import mark, save
from tests.infoos.test_opportunity_domain import item
from tests.infoos.test_opportunity_follow_up import prepared
from backend.application.usecases.opportunity_follow_up import set_opportunity_follow_up


def test_all_opportunities_pagination_filters_and_search(tmp_path):
    prepared(tmp_path)
    db = default_database_path(tmp_path)
    for number, step in [(2, "observe"), (3, "skip")]:
        _card(db, number, "2026-10-01T00:00:00Z", evaluated=False)
        mark(tmp_path, number)
        save(tmp_path, [item(f"card-{number}", next_step=step)])
    set_opportunity_follow_up(tmp_path, "card-1", "dismissed", actor="web")
    with sqlite3.connect(db) as conn:
        for number in range(1, 4):
            conn.execute("update opportunity_breakdowns set created_at=? where card_id=?",
                         (f"2026-10-0{number}T00:00:00Z", f"card-{number}"))
        conn.execute("update cards set source_author='唯一账号',title='唯一标题' where card_id='card-3'")
    with TestClient(create_app(tmp_path)) as client:
        first = client.get("/api/opportunities/all?limit=2").json()
        assert first["total"] == 3
        assert [row["card_id"] for row in first["items"]] == ["card-3", "card-2"]
        assert [row["next_step"] for row in first["items"]] == ["skip", "observe"]
        last = client.get("/api/opportunities/all?limit=2&offset=2").json()
        assert [row["card_id"] for row in last["items"]] == ["card-1"]
        assert last["items"][0]["stage"] == "archive"
        for query in ["card-3", "唯一账号", "唯一标题"]:
            assert client.get("/api/opportunities/all", params={"query": query}).json()["items"][0]["card_id"] == "card-3"
        for key, value, count in [("next_step", "observe", 1), ("stage", "archive", 1),
                                  ("stage", "unstaged", 1), ("platform", "not-present", 0),
                                  ("monetization_model", "ecommerce", 0)]:
            assert client.get("/api/opportunities/all", params={key: value}).json()["total"] == count
        assert client.get("/api/opportunities/board?query=card-1").json()["columns"]["archive"]["count"] == 1
        assert client.get("/api/opportunities/all?query=card-3&next_step=observe").json()["total"] == 0


@pytest.mark.parametrize("step,follow_up,stage", [
    ("skip", None, None), ("observe", None, "inbox"), ("verify", "following", "plan"),
    ("verify", "dismissed", "archive"),
])
def test_detail_and_preview_do_not_depend_on_board_membership(tmp_path, step, follow_up, stage):
    prepared(tmp_path)
    save(tmp_path, [item(next_step=step)])
    if follow_up is not None:
        set_opportunity_follow_up(tmp_path, "card-1", follow_up, actor="web")
    with TestClient(create_app(tmp_path)) as client:
        detail = client.get("/api/opportunities/card-1").json()
        assert detail["stage"] == stage
        assert detail["author"] is not None
        assert client.get("/api/card-preview/card-1").json()["opportunity"] == {"has_breakdown": True, "next_step": step}
        _card(default_database_path(tmp_path), 2, "2026-10-01T00:00:00Z", evaluated=False)
        with sqlite3.connect(default_database_path(tmp_path)) as conn:
            conn.execute("update cards set content_hash='hash' where card_id='card-2'")
        assert client.get("/api/card-preview/card-2").json()["opportunity"] is None


def test_list_question_counts_and_complete_detail_arrays(tmp_path):
    questions = ["投入多少？", "如何获客？", "收益能否核实？"]
    prepared(tmp_path)
    evidence = ["原文提供了产品价格。", "作者说明了交付流程。"]
    save(tmp_path, [item(next_step="observe", evidence=evidence, verify_questions=questions)])
    with TestClient(create_app(tmp_path)) as client:
        board = client.get("/api/opportunities/board?include_observe=true").json()
        page = client.get("/api/opportunities/all").json()
        detail = client.get("/api/opportunities/card-1").json()
    assert board["columns"]["inbox"]["items"][0]["question_count"] == len(questions)
    assert page["items"][0]["question_count"] == len(questions)
    assert detail["evidence"] == evidence
    assert detail["verify_questions"] == questions
