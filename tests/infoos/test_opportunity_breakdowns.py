"""Transactional opportunity workflow, pagination and authenticated MCP contract."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.submit_opportunity_breakdowns import submit_opportunity_breakdowns
from backend.domain.provider_recovery import is_intelligence_evaluation
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository
from backend.infrastructure.storage.opportunity_candidates import candidate_query
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository
from backend.read_models.card_preview import read_card_preview_detail
from backend.read_models.opportunities import list_opportunities, list_opportunity_candidates
from tests.infoos.test_agent_evaluations import item as evaluation_item, seed, submit as evaluate
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_mcp_collect import call, invoke
from tests.infoos.test_opportunity_domain import item

START, END = "2026-09-28T12:00:00Z", "2026-09-28T13:00:00Z"


def mark(base, number=1, status="matched", *, nested=True):
    payload = {"prefilter": {"status": status, "rules_version": "prefilter_v3", "hits": [
        {"field": "desc", "token": "培训"}]}}
    with sqlite3.connect(default_database_path(base)) as conn:
        conn.execute("update raw_items set raw_payload=? where raw_item_id=?",
                     (json.dumps({"raw_metadata": payload} if nested else payload), f"card-{number}"))


def save(base, items=None):
    return submit_opportunity_breakdowns(base, " agent:test ", items or [item()], token_id="writer")["results"]


def test_atomic_overwrite_independent_blocks_and_finalize(tmp_path, monkeypatch):
    seed(tmp_path)
    mark(tmp_path)
    evaluate(tmp_path, [evaluation_item()])
    blocks = CardContentBlockRepository(tmp_path)
    first = save(tmp_path)[0]
    assert first["status"] == "accepted"
    stored = OpportunityBreakdownRepository(tmp_path).latest_for_card("card-1")
    assert stored["token_id"] == "writer" and stored["evaluator"] == "agent:test"
    assert stored["card_version"] == 1 and stored["lens_version"] == "opportunity_v1"
    assert save(tmp_path)[0]["finalize_task_id"] == first["finalize_task_id"]
    second = save(tmp_path, [item(offer="新课程")])[0]
    assert second["breakdown_id"] == first["breakdown_id"]
    assert second["finalize_task_id"] != first["finalize_task_id"]
    updated = OpportunityBreakdownRepository(tmp_path).latest_for_card("card-1")
    assert updated["created_at"] == stored["created_at"] and updated["offer"] == "新课程"
    evaluate(tmp_path, [evaluation_item(summary="更新摘要")])
    rows = blocks.list_for_card("card-1")
    assert len(rows) == 3 and sum(is_intelligence_evaluation(b) for b in rows) == 2
    breakdown = next(b for b in rows if b["block_kind"] == "opportunity_breakdown")
    assert "新课程" in breakdown["body"] and breakdown["provenance"] == "intelligence.opportunity_breakdown"
    assert not is_intelligence_evaluation(breakdown)
    assert any(b["block_kind"] == "opportunity_breakdown" for b in read_card_preview_detail(tmp_path, "card-1")["body_blocks"])
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from opportunity_breakdowns").fetchone()[0] == 1
    tasks = TaskRepository.for_base_dir(tmp_path).list_tasks()
    task = next(t for t in tasks if t.task_id == second["finalize_task_id"])
    assert task.input_version.startswith("opportunity:1:") and task.task_type == TaskType.CARD_FINALIZE
    monkeypatch.setattr("backend.application.tasks.card_delivery_transitions.enqueue_webhooks",
                        lambda *args, **kwargs: pytest.fail("breakdown publication must not enqueue webhook"))
    run = run_due_tasks(tmp_path, now="2099-01-01T00:00:00Z", task_types=[TaskType.CARD_FINALIZE])
    assert run["results"] and all(r["downstream_task_ids"] == [] for r in run["results"])
    assert CardRepository(tmp_path).get_card("card-1")["version"] == 2
    assert len(blocks.list_for_card("card-1")) == 3


def test_rejections_and_transaction_rollback(tmp_path, monkeypatch):
    seed(tmp_path)
    assert save(tmp_path)[0]["error"] == "not_opportunity_candidate"
    mark(tmp_path)
    results = save(tmp_path, [item(), item("missing"), item(credibility=-1), {}])
    assert [r["status"] for r in results] == ["accepted", "rejected", "rejected", "rejected"]
    assert results[1]["error"] == "card_missing"
    import backend.application.usecases.submit_opportunity_breakdowns as module
    def fail(*args, **kwargs):
        raise ValueError("enqueue_failure")
    monkeypatch.setattr(module, "enqueue_task", fail)
    before = OpportunityBreakdownRepository(tmp_path).latest_for_card("card-1")
    old_blocks = CardContentBlockRepository(tmp_path).list_for_card("card-1")
    old_tasks = TaskRepository.for_base_dir(tmp_path).list_tasks()
    assert save(tmp_path, [item(offer="回滚课程")])[0]["error"] == "enqueue_failure"
    assert OpportunityBreakdownRepository(tmp_path).latest_for_card("card-1") == before
    assert CardContentBlockRepository(tmp_path).list_for_card("card-1") == old_blocks
    assert TaskRepository.for_base_dir(tmp_path).list_tasks() == old_tasks
    for evaluator, items in [(" ", [item()]), ("x" * 201, [item()]), ("agent", []), ("agent", [item()] * 21)]:
        with pytest.raises(ValueError):
            submit_opportunity_breakdowns(tmp_path, evaluator, items, token_id="writer")


def test_candidates_window_cursor_versions_and_indexes(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    db = default_database_path(tmp_path)
    for number, stamp, status in [(2, START, "unknown"), (3, END, "matched"),
                                   (4, START, "unmatched"), (5, START, "missed")]:
        _card(db, number, stamp, evaluated=False)
        mark(tmp_path, number, status, nested=False)
    args = {"since": START, "until": END}
    page = list_opportunity_candidates(tmp_path, **args, limit=1)
    assert page["items"][0]["card_id"] == "card-1"
    assert page["items"][0]["prefilter_summary"]["hits"] == [{"field": "desc", "token": "培训"}]
    second = list_opportunity_candidates(tmp_path, **args, limit=1, cursor=page["next_cursor"])
    assert [r["card_id"] for r in second["items"]] == ["card-2"] and second["next_cursor"] is None
    assert list_opportunity_candidates(tmp_path, since="2026-09-28T12:00:00.100Z", until=END)["items"] == []
    for changes in ({"cursor": "bad"}, {"cursor": page["next_cursor"], "only_unbroken": False},
                    {"since": "bad"}, {"since": END, "until": START}, {"limit": 0}):
        with pytest.raises(ValueError):
            list_opportunity_candidates(tmp_path, **{**args, **changes})
    save(tmp_path)
    assert [r["card_id"] for r in list_opportunity_candidates(tmp_path, **args)["items"]] == ["card-2"]
    assert list_opportunity_candidates(tmp_path, **args, only_unbroken=False)["items"][0]["has_current_breakdown"]
    with sqlite3.connect(db) as conn:
        conn.execute("update raw_items set revision=revision+1 where raw_item_id='card-1'")
    assert list_opportunity_candidates(tmp_path, **args)["items"][0]["card_id"] == "card-1"
    save(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("update opportunity_breakdowns set lens_version='old'")
        sql, params = candidate_query(since=START, until=END, after=(START, "card-1"), only_unbroken=True, limit=1, profile_version=1)
        plan = "\n".join(str(r) for r in conn.execute("explain query plan " + sql, params))
    assert "SEARCH r USING INDEX idx_raw_items_first_seen_source" in plan
    assert "SEARCH c USING INDEX idx_cards_opportunity_raw" in plan
    assert list_opportunity_candidates(tmp_path, **args)["items"][0]["card_id"] == "card-1"


def test_zsxq_uses_real_raw_source_not_card_labels(tmp_path):
    seed(tmp_path)
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("""insert into sources(source_id,platform,source_type,identifier,display_name,cadence_hours,
            connector_status,created_at,updated_at) values ('wind','zsxq','opportunity','wind','风向标',24,
            'supported',?,?)""", (START, START))
        conn.execute("update raw_items set source_id='wind'")
    assert len(list_opportunity_candidates(tmp_path, START)["items"]) == 1
    assert save(tmp_path)[0]["status"] == "accepted"
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update sources set source_type='topics'")
        conn.execute("update cards set source_platform='zsxq', source_type='opportunity'")
    assert save(tmp_path)[0]["error"] == "not_opportunity_candidate"
    assert list_opportunity_candidates(tmp_path, START, only_unbroken=False)["items"] == []


def test_latest_opportunities_filter_before_pagination(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    _card(default_database_path(tmp_path), 2, START, evaluated=False)
    mark(tmp_path, 2)
    save(tmp_path, [item(), item("card-2", next_step="verify", verify_questions=["交付是什么？"], replicability=90)])
    page = list_opportunities(tmp_path, limit=1)
    assert page["items"][0]["card_id"] == "card-2"
    assert page["items"][0]["verification_count"] == 0 and page["items"][0]["latest_verification_conclusion"] is None
    second = list_opportunities(tmp_path, limit=1, cursor=page["next_cursor"])
    assert second["items"][0]["card_id"] == "card-1" and second["next_cursor"] is None
    assert len(list_opportunities(tmp_path, next_step="verify", min_replicability=80)["items"]) == 1
    assert list_opportunities(tmp_path, since="2099-01-01T00:00:00Z")["items"] == []
    assert len(list_opportunities(tmp_path, since=page["items"][0]["created_at"])["items"]) == 1
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        conn.execute("update cards set version=2 where card_id='card-2'")
    save(tmp_path, [item("card-2", next_step="skip")])
    assert list_opportunities(tmp_path, next_step="verify")["items"] == []
    assert len(list_opportunities(tmp_path)["items"]) == 2
    for changes in ({"next_step": "bad"}, {"min_replicability": 101}, {"limit": 101}, {"cursor": "bad"},
                    {"cursor": page["next_cursor"], "next_step": "skip"}):
        with pytest.raises(ValueError):
            list_opportunities(tmp_path, **changes)


def test_mcp_scopes_errors_and_structured_budget(tmp_path):
    seed(tmp_path)
    mark(tmp_path)
    reader = create_token(tmp_path, name="reader", scopes=["intelligence:read"])
    writer = create_token(tmp_path, name="writer", scopes=["evaluation:write"])
    calls = [("get_opportunity_lens", {}), ("list_opportunity_candidates", {"since": START}),
             ("list_opportunities", {})]
    with TestClient(create_app(tmp_path)) as client:
        for name, args in calls:
            assert invoke(client, writer, name, **args)["isError"] is True
        assert invoke(client, reader, "submit_opportunity_breakdowns", evaluator="agent", items=[item()])["isError"]
        lens = call(client, reader, "get_opportunity_lens")
        assert lens["lens_version"] == "opportunity_v1" and "get_cards" in lens["workflow"]
        assert call(client, reader, "get_card", card_id="card-1")["opportunity"] is None
        assert call(client, reader, "list_opportunity_candidates", since=START)["items"][0]["deep_link"]["url"].endswith("/r/opportunities?card=card-1")
        assert call(client, writer, "submit_opportunity_breakdowns", evaluator="agent", items=[item()])["results"][0]["status"] == "accepted"
        for name, args in [("list_opportunity_candidates", {"since": "bad"}),
                           ("list_opportunities", {"cursor": "bad"}),
                           ("submit_opportunity_breakdowns", {"evaluator": "", "items": [item()]})]:
            result = call(client, writer if name.startswith("submit") else reader, name, **args)
            assert result["error"] and result["message"]
        single = call(client, reader, "get_card", card_id="card-1")
        batch = call(client, reader, "get_cards", card_ids=["card-1"])["items"][0]
        assert single["opportunity"] == batch["opportunity"]
        block = next(b for b in batch["content_blocks"] if b["block_kind"] == "opportunity_breakdown")
        assert block["body_in_opportunity"] and block["body"] == ""
        assert batch["truncated"] is False
        assert len(batch["content"]) + sum(len(b["body"]) for b in batch["content_blocks"]) <= 4000
