"""Local adapter -> durable run/raw/card -> authenticated MCP acceptance."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

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
from backend.application.workflows.fetch_support import build_connector
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.domain.sources.douyin_home_feed import home_feed_options
from backend.domain.sources.monetization_prefilter import RULES_VERSION, TOKENS, adapter_rules
from backend.domain.sources.versioning import source_fetch_input_version
from backend.infrastructure.storage.repositories import CardContentBlockRepository, RawItemRepository, RunRepository, SourceCursorRepository, SourceRepository
from tests.infoos.test_douyin_home_feed import source
from tests.infoos.test_intelligence_profile import _mcp


def test_options_projection_and_disabled_version_compatibility():
    disabled = database_source_to_connector_config(source())
    explicit = source(raw_metadata={"monetization_prefilter": False, "prefilter_comment_min_count": 90})
    assert database_source_to_connector_config(explicit) == disabled
    assert source_fetch_input_version(explicit) == source_fetch_input_version(source())
    enabled = source(raw_metadata={"monetization_prefilter": True})
    config = database_source_to_connector_config(enabled)
    assert config["command"][:-2] == disabled["command"]
    assert config["command"][-2] == "--prefilter"
    rules = json.loads(config["command"][-1])
    assert rules == adapter_rules(home_feed_options({}))
    assert rules["tokens"] == {key: list(values) for key, values in TOKENS.items()}
    assert rules["rules_version"] == RULES_VERSION == "prefilter_v5"
    assert (rules["comment_min_count"], rules["demand_min_comments"]) == (20, 2)
    assert source_fetch_input_version(enabled) != source_fetch_input_version(source())
    assert source_fetch_input_version(enabled) != source_fetch_input_version(
        source(raw_metadata={"monetization_prefilter": True, "prefilter_demand_min_comments": 3}))


@pytest.mark.parametrize("options", [
    {"monetization_prefilter": "true"}, {"monetization_prefilter": 1},
    {"prefilter_comment_min_count": -1}, {"prefilter_comment_min_count": 1000001},
    {"prefilter_comment_min_count": True}, {"prefilter_demand_min_comments": 0},
    {"prefilter_demand_min_comments": 21}, {"prefilter_demand_min_comments": 1.5},
])
def test_invalid_options(options):
    with pytest.raises(ValueError):
        home_feed_options(options)


def adapter_batch(statuses):
    """Execute real JS screening/session with bounded in-memory network fixtures."""
    script = """
      import { screenFeedItem } from './deploy/docker/opencli-adapters/douyin/feed-prefilter.js';
      import { collectFeed } from './deploy/docker/opencli-adapters/douyin/feed-session.js';
      const {config, statuses} = JSON.parse(process.argv[1]);
      const state = position => ({position, item: {
        aweme_id: String(1234567890123456n + BigInt(position)),
        url: 'https://www.douyin.com/video/' + String(1234567890123456n + BigInt(position)),
        author: '示例作者', author_url: 'https://www.douyin.com/user/example',
        title: '示例视频' + position, comment_count: 5, digg_count: 3, share_count: 1, collect_count: 2,
        is_ad: statuses[position-1] === 'skipped', is_live: statuses[position-1] === 'live',
        position,
      }});
      const result = await collectFeed({limit: statuses.length, read: async () => state(1),
        advance: async old => state(old.position+1), wait: async () => {}, withMeta: true,
        prefilter: async row => ({...row, ...await screenFeedItem(row, config, async (path, params) => {
          const status = statuses[row.position-1];
          if (status === 'unknown' || params.sec_user_id === 'unavailable') throw new Error('timeout');
          if (path.endsWith('/aweme/post/')) {
            if (status === 'mention_pinned_failed') throw new Error('timeout');
            return {status_code: 0, aweme_list: ['mention_pinned', 'mention_pinned_author'].includes(status)
              ? [{is_top: 1, desc: '课程'}, {is_top: true, desc: ''}] : []};
          }
          if (status.startsWith('mention_')) {
            if (params.sec_user_id === 'example') return {status_code: 0, user: {
              signature: ['mention_signature', 'mention_pinned_author'].includes(status) ? '@朋友'
                : status === 'mention_pinned_failed' ? '商务' : '日常生活',
              signature_extra: [{type: 0, sec_uid: 'target'},
                {type: 0, sec_uid: status === 'mention_pinned' ? 'second' : 'unavailable'}],
            }};
            return {status_code: 0, user: {sec_uid: params.sec_user_id,
              signature: status === 'mention_signature' ? 'V 商务' : '',
              r_fans_group_info: status === 'mention_group' ? {group: 1} : {},
            }};
          }
          return {status_code: 0, user: {signature: status === 'matched' ? '商务合作' : ['@', '：', '@ ：', 'v'].includes(status) ? status : '日常生活',
            r_fans_group_info: {}}};
        })})});
      console.log(JSON.stringify(result));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps({
        "config": adapter_rules(home_feed_options({})), "statuses": statuses,
    })], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def run_batch(tmp_path, monkeypatch, statuses):
    row = {**source(raw_metadata={"monetization_prefilter": True}), "connector_status": "supported"}
    SourceRepository(tmp_path).upsert_source(row)
    config = database_source_to_connector_config(row)
    connector = build_connector(tmp_path, config)
    connector.use_browser_window = False
    batch = adapter_batch(statuses)
    monkeypatch.setattr(connector, "_load_payload", lambda: batch)
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    monkeypatch.setattr("backend.application.workflows.acquisition._ensure_personalized_feed_login", lambda *a, **kw: None)
    task = Task("prefilter_fetch", TaskType.FETCH, "source", row["source_id"])
    artifacts = fetch_source(tmp_path, source_id=row["source_id"], task=task)
    return row, connector, task, artifacts, batch


def test_mixed_batch_misses_not_persisted_and_matching_card_readable_over_mcp(tmp_path, monkeypatch):
    row, connector, task, artifacts, batch = run_batch(
        tmp_path, monkeypatch, ["matched", "missed", "unknown", "skipped", "live"])
    run = RunRepository(tmp_path).get_run(task.task_id)
    summary = {"rules_version": RULES_VERSION, "checked": 3, "matched": 1, "missed": 1,
               "unknown": 1, "skipped": 2, "token_hits": {"商务": 1, "合作": 1},
               "mention_targets": 0, "mention_profiles_ok": 0, "mention_hit_items": 0,
               "mention_pinned_checked": 0, "mention_pinned_ok": 0, "mention_pinned_hit_items": 0}
    assert artifacts["prefilter_summary"] == run["raw_payload"]["prefilter_summary"] == summary
    assert run["artifacts_summary"]["prefilter_summary"] == summary
    assert artifacts["completeness_status"] == "complete"
    assert artifacts["observed_count"] == artifacts["visible_count"] == 5
    assert artifacts["fetched_count"] == 3
    assert artifacts["dedup_skipped_count"] == 1  # Existing live exclusion convention.
    misses = run["raw_payload"]["prefilter_misses"]
    assert misses == [{"aweme_id": batch["items"][1]["aweme_id"], "url": batch["items"][1]["url"],
                       "author_sec_uid": "example", "author": "示例作者", "author_signature": "日常生活",
                       "title": "示例视频2", "digg_count": 3, "comment_count": 5, "share_count": 1,
                       "collect_count": 2, "hits": [], "rules_version": RULES_VERSION, "mention_sec_uids": []}]
    raw = RawItemRepository(tmp_path).list_raw_items()
    assert len(raw) == len(artifacts["card_build_task_ids"]) == 3
    assert {item["raw_payload"]["raw_metadata"]["prefilter"]["status"] for item in raw} == {"matched", "unknown", "skipped"}
    assert RawItemRepository(tmp_path).get_for_connector(connector.connector_id, misses[0]["url"]) is None
    cursor = SourceCursorRepository(tmp_path).get(row["source_id"])["cursor_value"]
    assert misses[0]["url"] in json.loads(cursor.removeprefix("snapshot:"))
    repo = TaskRepository.for_base_dir(tmp_path)
    card_ids = []
    for task_id in artifacts["card_build_task_ids"]:
        built = handle_card_build(repo.get(task_id), tmp_path)
        card_id = built["artifacts"]["card_id"]
        card_ids.append(card_id)
        handle_card_finalize(Task(f"finalize_{card_id}", TaskType.CARD_FINALIZE, "card", card_id), tmp_path)
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        items = _mcp(client, token, "get_cards", {"card_ids": card_ids}).json()["result"]["structuredContent"]["items"]
    matched = items[0]
    blocks = {block["block_kind"]: block for block in matched["content_blocks"]}
    assert blocks["author_profile"]["body"] == "商务合作"
    assert "signature: 商务" in blocks["screening_evidence"]["body"]
    assert RULES_VERSION in blocks["screening_evidence"]["body"]
    assert matched["content"] == "示例视频1"  # Metadata must not replace original caption.
    persisted = CardContentBlockRepository(tmp_path).list_for_card(card_ids[0])
    assert next(b for b in persisted if b["block_kind"] == "screening_evidence")["raw_payload"]["status"] == "matched"
    assert "未知，按命中保留" in items[1]["content_blocks"][1]["body"]
    assert card_count(tmp_path) == 3
    # Same snapshot stays deduplicated while misses and observations remain auditable.
    again = fetch_source(tmp_path, source_id=row["source_id"], task=replace(task, task_id="prefilter_fetch_again"))
    assert again["fetched_count"] == 0
    assert again["dedup_skipped_count"] == 5
    assert again["prefilter_summary"] == summary
    assert again["completeness_status"] == "complete"


def test_all_missed_advances_cursor_without_raw_cards_or_media_tasks(tmp_path, monkeypatch):
    row, _, task, artifacts, _ = run_batch(tmp_path, monkeypatch, ["missed", "missed"])
    assert artifacts["raw_item_ids"] == artifacts["card_build_task_ids"] == []
    assert artifacts["fetched_count"] == artifacts["dedup_skipped_count"] == 0
    assert artifacts["visible_count"] == artifacts["observed_count"] == 2
    assert artifacts["completeness_status"] == "complete"
    assert len(RunRepository(tmp_path).get_run(task.task_id)["raw_payload"]["prefilter_misses"]) == 2
    assert len(json.loads(SourceCursorRepository(tmp_path).get(row["source_id"])["cursor_value"][9:])) == 2
    assert RawItemRepository(tmp_path).list_raw_items() == []
    assert card_count(tmp_path) == 0
    assert TaskRepository.for_base_dir(tmp_path).list_pending() == []


def test_fetch_task_persists_prefilter_summary_in_artifacts(tmp_path, monkeypatch):
    row, _, task, artifacts, _ = run_batch(tmp_path, monkeypatch, ["missed", "matched"])
    repo = TaskRepository.for_base_dir(tmp_path)
    durable = replace(task, task_id="prefilter_durable")
    repo.enqueue(durable)
    claimed = repo.claim_task_ids([durable.task_id], worker_id="prefilter-worker",
                                 now="2026-10-14T10:00:00Z", lease_expires_at="2026-10-14T11:00:00Z")[0]
    fetch_source(tmp_path, source_id=row["source_id"], task=claimed)
    stored = repo.get(durable.task_id).artifacts_summary
    assert stored["prefilter_summary"] == artifacts["prefilter_summary"]
    assert stored["acquisition_checkpoint"]["prefilter_summary"] == artifacts["prefilter_summary"]
    assert stored["completeness_status"] == "complete"


def test_mentions_survive_adapter_run_card_blocks_and_mcp(tmp_path, monkeypatch):
    _, _, task, artifacts, batch = run_batch(
        tmp_path, monkeypatch, ["mention_signature", "mention_group", "mention_missed"])
    run = RunRepository(tmp_path).get_run(task.task_id)
    summary = {"rules_version": "prefilter_v5", "checked": 3, "matched": 2, "missed": 1,
               "unknown": 0, "skipped": 0, "token_hits": {"@": 1, "v": 1, "商务": 1, "non_empty": 1},
               "mention_targets": 6, "mention_profiles_ok": 3, "mention_hit_items": 2,
               "mention_pinned_checked": 3, "mention_pinned_ok": 3, "mention_pinned_hit_items": 0}
    assert artifacts["prefilter_summary"] == run["raw_payload"]["prefilter_summary"] == summary
    assert run["artifacts_summary"]["prefilter_summary"] == summary
    miss = run["raw_payload"]["prefilter_misses"][0]
    assert miss["mention_sec_uids"] == ["target", "unavailable"]
    assert "mentions" not in miss  # Miss records retain only target identifiers.
    assert artifacts["fetched_count"] == 2
    repo = TaskRepository.for_base_dir(tmp_path)
    card_ids = []
    for index, task_id in enumerate(artifacts["card_build_task_ids"]):
        card_id = handle_card_build(repo.get(task_id), tmp_path)["artifacts"]["card_id"]
        handle_card_finalize(Task(f"finalize_{card_id}", TaskType.CARD_FINALIZE, "card", card_id), tmp_path)
        card_ids.append(card_id)
        blocks = CardContentBlockRepository(tmp_path).list_for_card(card_id)
        for block in blocks:
            if block["block_kind"] in {"author_profile", "screening_evidence"}:
                assert block["raw_payload"]["mentions"] == batch["items"][index]["prefilter"]["mentions"]
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        items = _mcp(client, token, "get_cards", {"card_ids": card_ids}).json()["result"]["structuredContent"]["items"]
    for index, item in enumerate(items):
        blocks = {block["block_kind"]: block for block in item["content_blocks"]}
        author = "@朋友" if index == 0 else "日常生活"
        signature = "V 商务" if index == 0 else "简介为空。"
        assert blocks["author_profile"]["body"] == (
            f"{author}\n\n被 @ 账号简介\n\ntarget：\n{signature}\n置顶作品：\n首屏无置顶作品。"
            "\n\nunavailable：\n简介未取得。\n置顶作品未取得（未请求）。")
        hit = "mention_signature: 商务" if index == 0 else "mention_fans_group: non_empty"
        assert hit in blocks["screening_evidence"]["body"]
        assert "prefilter_v5" in blocks["screening_evidence"]["body"]
        assert item["content"] == f"示例视频{index + 1}"


def test_pinned_evidence_survives_run_blocks_and_mcp_with_per_item_counts(tmp_path, monkeypatch):
    _, _, task, artifacts, batch = run_batch(tmp_path, monkeypatch, [
        "mention_pinned", "mention_pinned_author", "mention_pinned_failed", "mention_pinned_missed",
    ])
    summary = {"rules_version": "prefilter_v5", "checked": 4, "matched": 3, "missed": 1,
               "unknown": 0, "skipped": 0, "token_hits": {"课程": 2, "@": 1, "商务": 1},
               "mention_targets": 8, "mention_profiles_ok": 5, "mention_hit_items": 0,
               "mention_pinned_checked": 5, "mention_pinned_ok": 4, "mention_pinned_hit_items": 2}
    run = RunRepository(tmp_path).get_run(task.task_id)
    assert artifacts["prefilter_summary"] == run["raw_payload"]["prefilter_summary"] == summary
    assert run["artifacts_summary"]["prefilter_summary"] == summary
    assert artifacts["fetched_count"] == 3
    assert "mentions" not in run["raw_payload"]["prefilter_misses"][0]
    assert batch["items"][0]["prefilter"]["mentions"][1]["pinned_ok"] is True
    repo = TaskRepository.for_base_dir(tmp_path)
    card_ids = []
    for index, task_id in enumerate(artifacts["card_build_task_ids"]):
        card_id = handle_card_build(repo.get(task_id), tmp_path)["artifacts"]["card_id"]
        handle_card_finalize(Task(f"finalize_{card_id}", TaskType.CARD_FINALIZE, "card", card_id), tmp_path)
        card_ids.append(card_id)
        blocks = {b["block_kind"]: b for b in CardContentBlockRepository(tmp_path).list_for_card(card_id)}
        for kind in ("author_profile", "screening_evidence"):
            assert blocks[kind]["raw_payload"]["mentions"] == batch["items"][index]["prefilter"]["mentions"]
        if index < 2:
            assert "置顶作品：\n1. 课程\n2. 描述为空。" in blocks["author_profile"]["body"]
            assert "mention_pinned: 课程" in blocks["screening_evidence"]["body"]
        else:
            assert "置顶作品未取得。" in blocks["author_profile"]["body"]
            assert "置顶作品未取得（未请求）。" in blocks["author_profile"]["body"]
            assert "mention_pinned:" not in blocks["screening_evidence"]["body"]
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(create_app(tmp_path)) as client:
        items = _mcp(client, token, "get_cards", {"card_ids": card_ids}).json()["result"]["structuredContent"]["items"]
    for index, item in enumerate(items):
        blocks = {b["block_kind"]: b for b in item["content_blocks"]}
        assert "prefilter_v5" in blocks["screening_evidence"]["body"]
        if index < 2:
            assert "1. 课程" in blocks["author_profile"]["body"]
            assert "mention_pinned: 课程" in blocks["screening_evidence"]["body"]
        assert item["content"] == f"示例视频{index + 1}"
