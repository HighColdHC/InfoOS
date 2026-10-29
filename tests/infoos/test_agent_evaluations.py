from __future__ import annotations

import sqlite3
import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.executor import run_due_tasks
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType, TaskStatus
from backend.application.usecases.api_tokens import create_token
from backend.application.usecases.daily_brief import build_daily_brief
from backend.application.usecases.interest_profiles import push_profile
from backend.application.usecases.submit_evaluations import submit_evaluations
from backend.domain.intelligence.rubric import RUBRIC_VERSION
from backend.domain.provider_recovery import is_intelligence_evaluation
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.infrastructure.storage.database import default_database_path
from backend.infrastructure.storage.repositories import CardRepository, CardContentBlockRepository
from backend.read_models.new_cards import list_new_cards
from tests.infoos.test_daily_intelligence import _card
from tests.infoos.test_intelligence_profile import _mcp

PROFILE = """## 主线与KR
- KR
## 进行中项目
- InfoOS
## 正在追问的问题
- 问题
## 暂放方向
- 游戏 [[暂停决策]]
## 长期领域
- AI
"""


def item(card_id="card-1", **changes):
    return {"card_id": card_id, "quality": 80, "relevance": 90, "attribution_kind": "project",
            "attribution_ref": "InfoOS", "content_type": "method", "suggested_action": "read",
            "exclusion": "low_quality" if changes.get("quality", 80) < 60 else "paused" if changes.get("attribution_ref") == "游戏 [[暂停决策]]" else None,
            "extract_kinds": [], "paused_landmark": False, "summary": "中文摘要", "relation_note": "用于InfoOS项目。", **changes}


def seed(base, profile=True):
    if profile:
        push_profile(base, PROFILE, source="test")
    _card(default_database_path(base), 1, "2026-09-28T12:00:00Z", evaluated=False)
    with sqlite3.connect(default_database_path(base)) as conn:
        conn.execute("update cards set content_hash=? where card_id='card-1'", ("sha256:" + "a" * 64,))


def submit(base, items):
    return submit_evaluations(base, "codex:gpt-6.1-sol", items, token_id="writer")["results"]


def test_submit_partial_normalization_and_profile_missing(tmp_path):
    seed(tmp_path, profile=False)
    assert submit(tmp_path, [item()])[0]["reason"] == "profile_missing"
    push_profile(tmp_path, PROFILE, source="test")
    results = submit(tmp_path, [item(), item("missing"), item(content_type="bad"), item(quality=101),
                                item(suggested_action=[]), {"card_id": "card-1"}])
    assert [r["status"] for r in results] == ["accepted", "rejected", "rejected", "rejected", "rejected", "rejected"]
    assert results[1]["reason"] == "card_missing"
    submit(tmp_path, [item(attribution_ref="unknown", relevance=10)])
    evaluation = CardEvaluationRepository(tmp_path).latest_for_card("card-1")
    assert (evaluation["attribution_kind"], evaluation["attribution_ref"]) == ("exploration", None)
    submit(tmp_path, [item(attribution_ref="游戏 [[暂停决策]]", paused_landmark=True)])
    evaluation = CardEvaluationRepository(tmp_path).latest_for_card("card-1")
    assert (evaluation["attribution_kind"], evaluation["relevance"], evaluation["paused_landmark"]) == ("paused", None, 1)
    assert evaluation["prompt_version"] == RUBRIC_VERSION
    assert evaluation["provider_id"] == "agent:writer" and evaluation["receipt_id"] is None
    for evaluator in ("", " " * 2, "x" * 201):
        with pytest.raises(ValueError, match="evaluator"):
            submit_evaluations(tmp_path, evaluator, [item()], token_id="writer")
    with pytest.raises(ValueError, match="1–20"):
        submit(tmp_path, [item()] * 21)


def test_overwrite_blocks_threshold_and_finalize_without_llm(tmp_path, monkeypatch):
    seed(tmp_path)
    blocks = CardContentBlockRepository(tmp_path)
    for kind in ("intel_summary", "relation_note"):
        blocks.upsert_block({"block_id": f"old-{kind}", "card_id": "card-1", "position": 0,
                             "layer": "intelligence", "block_kind": kind, "status": "enriched", "body": "old"})
    first = submit(tmp_path, [item()])[0]
    same = submit(tmp_path, [item()])[0]
    assert first["finalize_task_id"] == same["finalize_task_id"]
    assert {b["body"] for b in blocks.list_for_card("card-1")} == {"中文摘要", "用于InfoOS项目。"}
    submit(tmp_path, [item(summary="替换摘要")])
    with sqlite3.connect(default_database_path(tmp_path)) as conn:
        assert conn.execute("select count(*) from card_evaluations").fetchone()[0] == 1
    assert CardEvaluationRepository(tmp_path).latest_for_card("card-1")["summary"] == "替换摘要"
    low = submit(tmp_path, [item(quality=59)])[0]
    assert not low["passed_quality"]
    assert not any(is_intelligence_evaluation(b) for b in blocks.list_for_card("card-1"))
    # This branch must short-circuit before resolving any source LLM binding.
    monkeypatch.setattr("backend.application.tasks.card_delivery_transitions.enqueue_webhooks",
                        lambda *args, **kwargs: pytest.fail("agent finalize must not enqueue webhook"))
    result = run_due_tasks(tmp_path, now="2099-01-01T00:00:00Z", task_types=[TaskType.CARD_FINALIZE])
    assert result["results"] and all(r["downstream_task_ids"] == [] for r in result["results"])
    assert all(t.task_type is TaskType.CARD_FINALIZE and t.status is TaskStatus.SUCCESS
               for t in TaskRepository.for_base_dir(tmp_path).list_tasks())
    card = CardRepository(tmp_path).get_card("card-1")
    assert card["version"] == 2 and card["content_hash"].startswith("sha256:")


def test_write_transaction_rolls_back_blocks_evaluation_and_task(tmp_path, monkeypatch):
    seed(tmp_path)
    import backend.application.usecases.submit_evaluations as module
    def fail(*args, **kwargs):
        raise ValueError("enqueue_failure")
    monkeypatch.setattr(module, "enqueue_task", fail)
    assert submit(tmp_path, [item()])[0]["reason"] == "enqueue_failure"
    assert CardEvaluationRepository(tmp_path).latest_for_card("card-1") is None
    assert CardContentBlockRepository(tmp_path).list_for_card("card-1") == []
    assert TaskRepository.for_base_dir(tmp_path).list_pending() == []


def test_new_cards_window_cursor_profile_rubric_and_platform(tmp_path):
    seed(tmp_path)
    db = default_database_path(tmp_path)
    _card(db, 2, "2026-09-28T12:00:01Z", evaluated=False)
    _card(db, 3, "2026-09-28T12:00:00Z", evaluated=False)
    _card(db, 4, "2026-09-28T13:00:00Z", evaluated=False)
    with sqlite3.connect(db) as conn:
        conn.execute("update cards set source_platform='x' where card_id='card-3'")
    args = {"since": "2026-09-28T12:00:00Z", "until": "2026-09-28T13:00:00Z"}
    page = list_new_cards(tmp_path, **args, limit=1)
    assert page["items"][0]["card_id"] == "card-1"
    second = list_new_cards(tmp_path, **args, limit=1, cursor=page["next_cursor"])
    assert second["items"][0]["card_id"] == "card-3"
    third = list_new_cards(tmp_path, **args, limit=1, cursor=second["next_cursor"])
    assert third["items"][0]["card_id"] == "card-2" and third["next_cursor"] is None
    assert [r["card_id"] for r in list_new_cards(tmp_path, **args, platform="rss")["items"]] == ["card-1", "card-2"]
    submit(tmp_path, [item()])
    assert [r["card_id"] for r in list_new_cards(tmp_path, **args)["items"]] == ["card-3", "card-2"]
    assert list_new_cards(tmp_path, **args, only_unevaluated=False)["items"][0]["has_evaluation"] is True
    push_profile(tmp_path, PROFILE + "\n更新", source="test")
    assert list_new_cards(tmp_path, **args)["items"][0]["card_id"] == "card-1"
    with pytest.raises(ValueError, match="invalid_cursor"):
        list_new_cards(tmp_path, **args, cursor=page["next_cursor"])
    with sqlite3.connect(db) as conn:
        conn.execute("update card_evaluations set profile_version=2, prompt_version='old_rubric'")
    assert list_new_cards(tmp_path, **args)["items"][0]["card_id"] == "card-1"
    for limit in (0, 101):
        with pytest.raises(ValueError, match="limit"):
            list_new_cards(tmp_path, **args, limit=limit)


def test_mcp_tools_scopes_batch_truncation_and_brief_e2e(tmp_path):
    seed(tmp_path, profile=False)
    reader = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    writer = create_token(tmp_path, name="agent", scopes=["intelligence:read", "profile:write", "evaluation:write"])["plaintext_token"]
    other = create_token(tmp_path, name="other", scopes=["cards:read"])["plaintext_token"]
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "long", "card_id": "card-1", "position": 0, "layer": "original",
        "block_kind": "card_content", "status": "preserved", "body": "文" * 5000,
        "segments": [{"text": "长" * 5000}],
    })
    app = create_app(tmp_path)
    calls = [("get_evaluation_rubric", {}), ("list_new_cards", {"since": "2026-09-28T00:00:00Z"}),
             ("get_cards", {"card_ids": ["card-1"]}),
             ("submit_evaluations", {"evaluator": "codex:gpt-6.1-sol", "items": [item()]})]
    with TestClient(app) as client:
        for name, args in calls:
            denied = _mcp(client, other, name, args).json()["result"]
            assert denied["isError"] is True and "insufficient_scope" in denied["content"][0]["text"]
        denied = _mcp(client, reader, "submit_evaluations", calls[-1][1]).json()["result"]
        assert denied["isError"] is True and "evaluation:write" in denied["content"][0]["text"]
        profile = _mcp(client, writer, "push_profile", {"markdown": PROFILE}).json()["result"]["structuredContent"]
        assert profile["profile_version"] == 1
        rubric = _mcp(client, reader, "get_evaluation_rubric").json()["result"]["structuredContent"]
        assert rubric["rubric_version"] == RUBRIC_VERSION and rubric["quality_threshold"] == 60
        assert rubric["profile_version"] == 1 and "get_profile" in rubric["workflow"]
        cards = _mcp(client, reader, "get_cards", {"card_ids": ["card-1", "missing"]}).json()["result"]["structuredContent"]["items"]
        assert cards[0]["truncated"] is True and cards[1]["error"] == "card_not_found"
        assert len(cards[0]["content"]) + sum(len(b["body"]) for b in cards[0]["content_blocks"]) <= 4000
        assert cards[0]["content_blocks"][0]["segments"] == []
        assert cards[0]["content_blocks"][0]["segments_omitted"] is True
        assert cards[0]["deep_link"]["url"].endswith("/r/card/card-1")
        listed = _mcp(client, reader, "list_new_cards", calls[1][1]).json()["result"]["structuredContent"]
        assert listed["items"][0]["card_id"] == "card-1"
        accepted = _mcp(client, writer, "submit_evaluations", calls[-1][1]).json()["result"]["structuredContent"]
        assert accepted["results"][0]["status"] == "accepted"
        assert accepted["results"][0]["deep_link"]["url"].endswith("/r/card/card-1")
        assert _mcp(client, reader, "list_new_cards", calls[1][1]).json()["result"]["structuredContent"]["items"] == []
        detail = _mcp(client, reader, "get_card", {"card_id": "card-1"}).json()["result"]["structuredContent"]
        assert detail["evaluation"]["model"] == "codex:gpt-6.1-sol"
        build_daily_brief(tmp_path, "2026-09-29")
        brief = _mcp(client, reader, "get_daily_brief", {"date": "2026-09-29"}).json()["result"]["structuredContent"]["brief"]
        assert brief["layout"] == "knowledge_v1"
        assert brief["items"] == []
        too_many = _mcp(client, reader, "get_cards", {"card_ids": ["card-1"] * 21}).json()["result"]
        assert too_many["isError"] is True


@pytest.mark.parametrize("content,other,expected,truncated", [
    ("文" * 3000, "录" * 5000, (3000, 1000), True),
    ("文" * 5000, "录" * 50, (4000, 0), True),
    ("文" * 5000, "", (4000, 0), True),
    ("短正文", "转录", (3, 2), False),
])
def test_bounded_card_prioritizes_original_text_once(content, other, expected, truncated):
    from backend.api.mcp.evaluation_tools import _bounded_card
    blocks = [{"layer": "original", "block_kind": "text", "segments": [], "body": content},
              {"layer": "derived", "block_kind": "transcript", "segments": [], "body": other}]
    result = _bounded_card({"content": content, "external_content": "", "opportunity": None, "content_blocks": blocks})
    assert (len(result["content"]), len(result["content_blocks"][1]["body"])) == expected
    assert result["truncated"] is truncated
    assert result["content_blocks"][0]["body_in_content"] is True
    assert result["content_blocks"][0]["body"] == ""
    assert len(result["content"]) + sum(len(b["body"]) for b in result["content_blocks"]) <= 4000
    assert blocks[0]["body"] == content


def test_bounded_card_original_parts_and_segments():
    from backend.api.mcp.evaluation_tools import _bounded_card
    result = _bounded_card({"content": "第一段\n\n第二段", "external_content": "", "opportunity": None, "content_blocks": [
        {"layer": "original", "block_kind": "text", "segments": [], "body": "第一段"},
        {"layer": "original", "block_kind": "text", "body": "第二段", "segments": [{"text": "第二段"}]},
    ]})
    assert all(b["body_in_content"] and b["body"] == "" for b in result["content_blocks"])
    assert all(b["segments"] == [] for b in result["content_blocks"])
    assert "segments_omitted" not in result["content_blocks"][0]
    assert result["content_blocks"][1]["segments_omitted"] is True
    assert result["truncated"] is False
    assert len(result["content"]) + sum(len(b["body"]) for b in result["content_blocks"]) <= 4000


@pytest.mark.parametrize("changes,normalized,warnings", [
    ({}, ("project", "InfoOS", 90, False), []),
    ({"attribution_kind": "paused", "attribution_ref": "unknown", "relevance": None,
      "paused_landmark": True}, ("exploration", None, 0, False),
     ["attribution_ref_not_in_profile", "attribution_kind_normalized", "relevance_normalized", "paused_landmark_cleared"]),
    ({"attribution_ref": "unknown"}, ("domain", None, 90, False),
     ["attribution_ref_not_in_profile", "attribution_kind_normalized"]),
    ({"attribution_ref": "游戏 [[暂停决策]]", "paused_landmark": True},
     ("paused", "游戏 [[暂停决策]]", None, True), ["attribution_kind_normalized", "relevance_normalized"]),
    ({"paused_landmark": True}, ("project", "InfoOS", 90, False), ["paused_landmark_cleared"]),
])
def test_submit_reports_normalized_fields(tmp_path, changes, normalized, warnings):
    seed(tmp_path)
    result = submit(tmp_path, [item(**changes)])[0]
    assert result["status"] == "accepted"
    keys = ("attribution_kind", "attribution_ref", "relevance", "paused_landmark")
    assert result["normalized"] == dict(zip(keys, normalized))
    if warnings:
        assert result["warnings"] == warnings
    else:
        assert "warnings" not in result
    stored = CardEvaluationRepository(tmp_path).latest_for_card("card-1")
    assert all(stored[k] == result["normalized"][k] for k in keys)


@pytest.mark.parametrize("field", ["summary", "relation_note"])
def test_submit_text_length_after_trimming(tmp_path, field):
    seed(tmp_path)
    results = submit(tmp_path, [item(**{field: " " + "文" * 501 + " "}),
                                item(**{field: " \n" + "文" * 500 + "\n "})])
    assert results[0]["reason"] == f"evaluation_{field}_too_long"
    assert results[1]["status"] == "accepted"
    assert CardEvaluationRepository(tmp_path).latest_for_card("card-1")[field] == "文" * 500
