"""Strong-only qualification, retained weak evidence, and Python/page parity."""
import json
from pathlib import Path
import subprocess

import pytest

from backend.domain.sources import monetization_prefilter as policy
from backend.domain.sources.douyin_home_feed import home_feed_options
from backend.domain.sources.versioning import source_fetch_input_version
from backend.domain.sources.xiaohongshu_prefilter import run_evidence
from backend.infrastructure.connectors.xiaohongshu_profiles import enrich_profiles
from backend.infrastructure.storage.repositories import RunRepository
from tests.infoos.test_monetization_prefilter import run_batch as douyin_batch
from tests.infoos.test_xiaohongshu_prefilter import run_batch as xhs_batch, source
from tests.infoos.test_xiaohongshu_profiles import record, profile, rules


def test_python_and_javascript_share_every_token_strength_and_verdict():
    configs = [policy.adapter_rules(home_feed_options({})), {**rules(), "comment_min_count": 20}]
    cases = []
    for config in configs:
        tokens = [t for category, values in config["tokens"].items() if category != "comment_demand" for t in values]
        for text in [*tokens, "@ ：", "@ 商务", "Vlog", "日常", "私戳", "DD"]:
            cases.append({"config": config, "text": text})
    script = """
      import { readFileSync } from 'node:fs';
      import { screenFeedItem } from './deploy/docker/opencli-adapters/douyin/feed-prefilter.js';
      const results = [];
      for (const {config, text} of JSON.parse(readFileSync(0, 'utf8'))) {
        const row = {author_url: 'https://www.douyin.com/user/fixture', comment_count: 0};
        results.push((await screenFeedItem(row, config, async () => ({status_code: 0,
          user: {signature: text, r_fans_group_info: {}}}))).prefilter);
      }
      console.log(JSON.stringify(results));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(cases),
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, check=True)
    for case, actual in zip(cases, json.loads(result.stdout), strict=True):
        expected = policy.xhs_text_hits(case["text"], "signature", case["config"])
        assert actual["hits"] == expected, case
        assert actual["status"] == ("matched" if policy.has_strong_hit(expected) else "missed"), case


@pytest.mark.parametrize("text", policy.XHS_WEAK_TOKENS + ("@ 👇 戳 DD",))
def test_xhs_weak_hits_continue_to_profile_and_survive_misses(text):
    calls = []
    def load(command):
        calls.append(command)
        return [profile(description="@", pinned_titles=["👇"])]
    row = enrich_profiles([record(text=text)], rules(), load)[0]
    evidence = row["prefilter"]
    assert len(calls) == 1 and evidence["status"] == "missed"
    assert evidence["hits"] and all(hit["strength"] == "weak" for hit in evidence["hits"])
    assert {hit["field"] for hit in evidence["hits"]} == {"content", "author_signature", "author_pinned"}
    assert run_evidence([row], rules())["prefilter_misses"][0]["hits"] == evidence["hits"]


@pytest.mark.parametrize("changes", [{"description": "@ 私信"}, {"pinned_titles": ["👇 课程"]}])
def test_xhs_secondary_strong_hits_qualify_and_keep_weak_text(changes):
    row = enrich_profiles([record(text="@")], rules(), lambda _: [profile(**changes)])[0]
    assert row["prefilter"]["status"] == "matched"
    assert {hit["strength"] for hit in row["prefilter"]["hits"]} == {"strong", "weak"}


def test_xhs_weak_text_continues_to_comments_without_changing_threshold_or_unknown():
    comments = [{"text": "求教程", "is_author": False}] * 2
    def no_profile(_):
        pytest.fail("Sufficient demand must skip profiles")
    matched = enrich_profiles([record(text="@", comments=comments)], rules(), no_profile)[0]
    assert matched["prefilter"]["status"] == "matched"
    assert matched["prefilter"]["hits"][0] == {"field": "content", "token": "@", "strength": "weak"}
    missed = enrich_profiles([record(text="@", comments=comments[:1])], rules(), lambda _: [profile()])[0]
    assert missed["prefilter"]["status"] == "missed"
    unknown = enrich_profiles([record(text="@")], rules(), lambda _: [profile(profile_ok=False)])[0]
    assert unknown["prefilter"]["status"] == "unknown"
    assert unknown["prefilter"]["hits"] == [{"field": "content", "token": "@", "strength": "weak"}]


@pytest.mark.parametrize("platform", ["douyin", "xiaohongshu"])
def test_weak_only_batch_records_evidence_without_raw_cards_or_tasks(tmp_path, monkeypatch, platform):
    if platform == "douyin":
        _, _, task, artifacts, _ = douyin_batch(tmp_path, monkeypatch, ["@", "：", "@ ：", "v"])
    else:
        _, _, task, artifacts, _ = xhs_batch(tmp_path, monkeypatch, ["@", "👇", "dd"])
    assert artifacts["fetched_count"] == 0
    assert artifacts["raw_item_ids"] == artifacts["card_build_task_ids"] == []
    misses = RunRepository(tmp_path).get_run(task.task_id)["raw_payload"]["prefilter_misses"]
    assert len(misses) == (4 if platform == "douyin" else 3)
    assert all(miss["hits"] and all(hit["strength"] == "weak" for hit in miss["hits"]) for miss in misses)


@pytest.mark.parametrize("name", ["RULES_VERSION", "XHS_RULES_VERSION", "WEAK_TOKENS", "XHS_WEAK_TOKENS"])
def test_version_and_strength_fingerprints_are_platform_local(monkeypatch, name):
    xhs = source(raw_metadata={"monetization_prefilter": True})
    dy = {**xhs, "platform": "douyin"}
    before = [source_fetch_input_version(row) for row in (dy, xhs)]
    monkeypatch.setattr(policy, name, "next" if name.endswith("VERSION") else ())
    after = [source_fetch_input_version(row) for row in (dy, xhs)]
    assert [a != b for a, b in zip(before, after)] == ([False, True] if name.startswith("XHS") else [True, False])


@pytest.mark.parametrize("platform,version", [("douyin", "prefilter_v4"), ("xiaohongshu", "xhs_prefilter_v3")])
def test_legacy_evidence_without_strength_still_builds_and_reads(tmp_path, monkeypatch, platform, version):
    from backend.application.tasks.handlers.acquisition import handle_card_build
    from backend.application.tasks.repository import TaskRepository
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, RawItemRepository

    batch = douyin_batch if platform == "douyin" else xhs_batch
    _, _, _, artifacts, _ = batch(tmp_path, monkeypatch, ["matched" if platform == "douyin" else "课程"])
    raw_repo = RawItemRepository(tmp_path)
    row = raw_repo.list_raw_items()[0]
    raw = raw_repo.get_model_for_connector(row["connector_id"], row["raw_item_id"])
    legacy = {**raw.raw_metadata["prefilter"], "rules_version": version,
              "hits": [{"field": "signature", "token": "@"}], "status": "matched"}
    raw_repo.upsert_raw_items([raw.model_copy(update={"raw_metadata": {**raw.raw_metadata, "prefilter": legacy}})])
    task = TaskRepository.for_base_dir(tmp_path).get(artifacts["card_build_task_ids"][0])
    card_id = handle_card_build(task, tmp_path)["artifacts"]["card_id"]
    block = next(b for b in CardContentBlockRepository(tmp_path).list_for_card(card_id)
                 if b["block_kind"] == "screening_evidence")
    assert version in block["body"] and "signature: @" in block["body"]
    assert block["raw_payload"]["hits"] == legacy["hits"]
    assert block["raw_payload"]["status"] == "matched"
