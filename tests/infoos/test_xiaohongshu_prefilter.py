"""XHS rules and existing feed/note -> run -> card -> authenticated MCP."""
from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient

from tests.infoos._factories import card_count
from backend.app.main import create_app
from backend.application.tasks.handlers.acquisition import handle_card_build
from backend.application.tasks.handlers.card_finalize import handle_card_finalize
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskType
from backend.application.usecases.api_tokens import create_token
from backend.application.workflows.acquisition import fetch_source
from backend.application.workflows.fetch_commit import SourceConfigurationChangedError, _stored_source_version
from backend.application.workflows.fetch_support import build_connector
from backend.domain.sources.configuration import database_source_to_connector_config, validate_source_payload
from backend.domain.sources import monetization_prefilter as policy
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.xiaohongshu_prefilter import screen_record
from backend.infrastructure.storage.repositories import RawItemRepository, RunRepository, SourceCursorRepository, SourceRepository
from tests.infoos.test_intelligence_profile import _mcp


def source(**changes):
    return validate_source_payload({"source_id": "xhs_feed", "platform": "xiaohongshu",
                                    "source_type": "home_feed", "identifier": "recommended",
                                    "cadence_hours": 5, "enabled": True, **changes})


def test_disabled_projection_and_version_remain_identical():
    baseline = source()
    disabled = source(raw_metadata={"monetization_prefilter": False, "prefilter_demand_min_comments": 9})
    config = database_source_to_connector_config(baseline)
    assert database_source_to_connector_config(disabled) == config
    assert config["command"] == ["xiaohongshu", "feed", "--limit", "20"]
    assert config["detail_command_template"] == ["xiaohongshu", "note", "{url}"]
    assert source_fetch_input_version(disabled) == source_fetch_input_version(baseline)
    enabled = database_source_to_connector_config(source(raw_metadata={"monetization_prefilter": True}))
    assert enabled["command"] == config["command"]
    assert enabled["detail_command_template"] == ["xiaohongshu", "screening-note", "{url}"]
    assert enabled["xhs_prefilter_rules"]["rules_version"] == "xhs_prefilter_v4"
    on = source(raw_metadata={"monetization_prefilter": True})
    assert source_fetch_input_version(on) != source_fetch_input_version(baseline)
    assert source_fetch_input_version(on) != source_fetch_input_version(source(raw_metadata={
        "monetization_prefilter": True, "prefilter_demand_min_comments": 3}))


@pytest.mark.parametrize("metadata", [{"monetization_prefilter": "true"}, {"monetization_prefilter": 1},
                                      {"monetization_prefilter": True, "prefilter_demand_min_comments": 0}])
def test_invalid_options(metadata):
    with pytest.raises(ValueError):
        source(raw_metadata=metadata)


def test_rules_fold_deduplicate_and_keep_demand_words_in_comments():
    rules = policy.xhs_rules({"monetization_prefilter": True})
    hits = policy.xhs_text_hits("🍠绿泡泡绿色软件 VX vx V❤ DD 滴滴看简介私我", "content", rules)
    assert {h["token"] for h in hits} >= {"🍠", "绿泡泡", "绿色软件", "vx", "v❤", "dd", "滴滴", "看简介", "私我"}
    assert [h["token"] for h in hits].count("vx") == 1
    assert policy.xhs_text_hits("求教程求分享多少钱蹲出教程怎么买哪里买", "content", rules) == []
    assert {h["token"] for h in policy.xhs_text_hits("求链接怎么买哪里买求教程求分享多少钱蹲出教程", "comments", rules,
                                                    comments=True)} == set(rules["tokens"]["comment_demand"])


def detail(content):
    return [{"field": "content", "value": content}, {"field": "tags", "value": "旅行"}]


def test_match_miss_unknown_and_successful_empty_body():
    rules = policy.xhs_rules({"monetization_prefilter": True})
    assert screen_record({"title": "私信"}, rules)["prefilter"]["status"] == "matched"
    for record in ({"title": "日常"}, {"content": "日常", "_opencli_detail_error": {"error": "failed"}},
                   {"content": "日常", "_opencli_detail_record": []}):
        assert screen_record(record, rules)["prefilter"]["status"] == "unknown"
    for content in ("日常", ""):
        assert screen_record({"content": content, "_opencli_detail_record": detail(content)}, rules)["prefilter"]["status"] == "unknown"


def run_batch(tmp_path, monkeypatch, texts, *, enabled=True):
    row = {**source(raw_metadata={"monetization_prefilter": enabled}), "connector_status": "supported"}
    SourceRepository(tmp_path).upsert_source(row)
    connector = build_connector(tmp_path, database_source_to_connector_config(row))
    connector.use_browser_window = False
    batch = [{"id": str(i), "title": "日常", "author": "示例", "likes": "3",
              "url": f"https://www.xiaohongshu.com/explore/{i}?xsec_token=fixture"}
             for i in range(len(texts))]
    calls = []

    def load(command=None):
        calls.append(command)
        if command is None:
            return batch
        if command[1] == "screening-profiles":
            return [{"user_id": user, "profile_checked": True, "profile_ok": True, "description": "日常",
                     "pinned_ok": True, "pinned_titles": [], "group_entry": False, "shop_entry": False}
                    for user in json.loads(command[-1])]
        index = next(i for i, item in enumerate(batch) if item["url"] == command[-1])
        if texts[index] is None:
            raise RuntimeError("fixture detail unavailable")
        return detail(texts[index]) + [
            {"field": "author_user_id", "value": f"author{index:04d}"},
            {"field": "comments_ok", "value": True}, {"field": "first_screen_comments", "value": []}]

    monkeypatch.setattr(connector, "_load_payload", load)
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    monkeypatch.setattr("backend.application.workflows.acquisition._ensure_personalized_feed_login", lambda *a, **kw: None)
    task = Task("xhs_prefilter_fetch", TaskType.FETCH, "source", row["source_id"])
    artifacts = fetch_source(tmp_path, source_id=row["source_id"], task=task)
    return row, connector, task, artifacts, calls


def test_mixed_batch_run_card_mcp_and_snapshot(tmp_path, monkeypatch):
    row, connector, task, artifacts, calls = run_batch(tmp_path, monkeypatch, ["绿泡泡课程", "日常", None])
    run = RunRepository(tmp_path).get_run(task.task_id)
    summary = run["raw_payload"]["prefilter_summary"]
    assert (summary["matched"], summary["missed"], summary["unknown"], summary["checked"]) == (1, 1, 1, 3)
    assert summary["token_hits"] == {"绿泡泡": 1, "课程": 1}
    assert artifacts["prefilter_summary"] == run["artifacts_summary"]["prefilter_summary"] == summary
    miss = run["raw_payload"]["prefilter_misses"][0]
    assert miss["note_id"] == "1" and "xsec_token" not in miss["url"]
    assert "content" not in miss and "_opencli_detail_record" not in miss
    assert artifacts["fetched_count"] == 2 and artifacts["dedup_skipped_count"] == 0
    assert artifacts["visible_count"] == 3
    assert artifacts.get("completeness_status") is None  # Upstream does not declare batch coverage.
    assert len(RawItemRepository(tmp_path).list_raw_items()) == 2
    assert len(calls) == 5  # feed, one note per observation, one profile batch
    cursor = SourceCursorRepository(tmp_path).get(row["source_id"])["cursor_value"]
    assert json.loads(cursor.removeprefix("snapshot:")) == ["0", "1", "2"]
    repo = TaskRepository.for_base_dir(tmp_path)
    ids = []
    for task_id in artifacts["card_build_task_ids"]:
        card_id = handle_card_build(repo.get(task_id), tmp_path)["artifacts"]["card_id"]
        handle_card_finalize(Task(f"finalize_{card_id}", TaskType.CARD_FINALIZE, "card", card_id), tmp_path)
        ids.append(card_id)
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        cards = _mcp(client, token, "get_cards", {"card_ids": ids}).json()["result"]["structuredContent"]["items"]
    blocks = {b["block_kind"]: b for b in cards[0]["content_blocks"]}
    assert "未读取" in blocks["author_profile"]["body"]
    assert "content: 绿泡泡" in blocks["screening_evidence"]["body"]
    assert "xhs_prefilter_v4" in blocks["screening_evidence"]["body"]
    assert cards[0]["content"] == "绿泡泡课程"
    assert "未知，按命中保留" in next(b["body"] for b in cards[1]["content_blocks"] if b["block_kind"] == "screening_evidence")
    again = fetch_source(tmp_path, source_id=row["source_id"], task=replace(task, task_id="again"))
    assert again["fetched_count"] == 0 and again["dedup_skipped_count"] == 3
    assert again["prefilter_summary"] == summary


def test_all_misses_create_no_raw_cards_or_media_tasks(tmp_path, monkeypatch):
    _, _, _, artifacts, _ = run_batch(tmp_path, monkeypatch, ["日常", "散步"])
    assert artifacts["fetched_count"] == 0 and artifacts["dedup_skipped_count"] == 0
    assert artifacts["raw_item_ids"] == artifacts["card_build_task_ids"] == []
    assert RawItemRepository(tmp_path).list_raw_items() == []
    assert card_count(tmp_path) == 0
    assert TaskRepository.for_base_dir(tmp_path).list_pending() == []


def test_disabled_output_untouched(tmp_path, monkeypatch):
    _, connector, task, artifacts, calls = run_batch(tmp_path, monkeypatch, ["日常", "课程"], enabled=False)
    assert len(calls) == 3 and artifacts["fetched_count"] == 2
    assert connector.prefilter_evidence == {}
    assert "prefilter_summary" not in RunRepository(tmp_path).get_run(task.task_id)["raw_payload"]
    assert all("prefilter" not in item["raw_payload"]["raw_metadata"] for item in RawItemRepository(tmp_path).list_raw_items())


def test_rule_version_uses_same_live_commit_fingerprint(tmp_path, monkeypatch):
    row, _, task, _, _ = run_batch(tmp_path, monkeypatch, ["课程"])
    original = source_fetch_input_version(row)
    assert _stored_source_version({**row, "raw_metadata": json.dumps(row["raw_metadata"])}) == original
    assert RunRepository(tmp_path).get_run(task.task_id)["artifacts_summary"]["source_input_version"] == original
    monkeypatch.setattr(policy, "XHS_RULES_VERSION", "xhs_prefilter_test_next")
    new = source_fetch_input_version(row)
    assert new != original
    assert _stored_source_version({**row, "raw_metadata": json.dumps(row["raw_metadata"])}) == new
    with pytest.raises(SourceConfigurationChangedError):
        fetch_source(tmp_path, source_id=row["source_id"], task=replace(task, task_id="stale", input_version=original))
    current = fetch_source(tmp_path, source_id=row["source_id"], task=replace(task, task_id="new", input_version=new))
    assert current["source_input_version"] == new


def test_commit_rechecks_configuration_changed_during_fetch(tmp_path, monkeypatch):
    row, connector, task, _, _ = run_batch(tmp_path, monkeypatch, ["课程"])
    version = source_fetch_input_version(row)
    load = connector._load_payload

    def change_during_load(command=None):
        result = load(command)
        SourceRepository(tmp_path).upsert_source({**row, "raw_metadata": {"monetization_prefilter": False}})
        return result

    monkeypatch.setattr(connector, "_load_payload", change_during_load)
    with pytest.raises(SourceConfigurationChangedError):
        fetch_source(tmp_path, source_id=row["source_id"], task=replace(task, task_id="race", input_version=version))
    assert RunRepository(tmp_path).get_run("race") is None


def test_filtering_does_not_promote_partial_batch_to_complete(tmp_path, monkeypatch):
    row, connector, task, _, _ = run_batch(tmp_path, monkeypatch, ["日常"])
    load = connector._load_payload

    def partial(command=None):
        data = load(command)
        return data if command else {"items": data, "acquisition": {"requested_count": 5, "observed_count": 1,
            "complete": False, "stop_reason": "feed_short", "stop_position": 1}}

    monkeypatch.setattr(connector, "_load_payload", partial)
    result = fetch_source(tmp_path, source_id=row["source_id"], task=replace(task, task_id="partial"))
    assert result["completeness_status"] == "partial"
    assert result["observed_count"] == 1 and result["fetched_count"] == 0
