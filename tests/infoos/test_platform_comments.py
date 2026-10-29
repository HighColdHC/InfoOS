from __future__ import annotations

import json

import pytest
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.enrichment.platform_comments import enrich_platform_comments
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository


def _completed(payload: object):
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")


def _card_payload(card_id: str, *, platform: str, raw_payload: dict | None = None) -> dict:
    return {
        "card_id": card_id,
        "source_id": None,
        "source_platform": platform,
        "source_type": "social_post",
        "source_url": f"https://example.com/{platform}/{card_id}",
        "source_author": "author",
        "title": "Title",
        "content": "Content",
        "summary": "Summary",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-07-02T01:00:00Z",
        "captured_at": "2026-07-02T01:01:00Z",
        "processed_at": "2026-07-02T01:02:00Z",
        "raw_payload": raw_payload or {},
    }


def test_xiaohongshu_comments_are_sorted_and_limited(monkeypatch, tmp_path):
    CardRepository(tmp_path).upsert_card(
        _card_payload(
            "card_xhs",
            platform="xiaohongshu",
            raw_payload={"title": "小红书笔记"},
        )
        | {"source_url": "https://www.xiaohongshu.com/search_result/1?xsec_token=abc"}
    )
    calls = []
    lease_kwargs = []

    def fake_run(command, **kwargs):
        calls.append(command)
        comments = [
            {"rank": i, "author": f"用户{i}", "text": f"评论{i}", "likes": i, "time": "刚刚"}
            for i in range(52)
        ]
        return _completed(comments)

    monkeypatch.setattr("backend.application.enrichment.platform_comments.subprocess.run", fake_run)
    monkeypatch.setattr(
        "backend.application.enrichment.platform_comments.opencli_tab_lease",
        lambda *_args, **kwargs: lease_kwargs.append(kwargs) or nullcontext(),
    )

    created = enrich_platform_comments("card_xhs", tmp_path)

    assert created == 1
    assert calls[0][1:3] == ["--profile", "infoos-fetch"]
    assert calls[0][3:6] == ["xiaohongshu", "comments", "https://www.xiaohongshu.com/search_result/1?xsec_token=abc"]
    assert "--limit" in calls[0]
    assert calls[0][calls[0].index("--site-session") + 1] == "persistent"
    assert calls[0][calls[0].index("--keep-tab") + 1] == "false"
    assert lease_kwargs == [{"close_tabs": False}]
    block = CardContentBlockRepository(tmp_path).list_for_card("card_xhs")[0]
    assert block["block_kind"] == "platform_comments"
    assert block["status"] == "enriched"
    assert "评论51" in block["body"]
    assert len(block["raw_payload"]["comments"]) == 50
    assert block["raw_payload"]["comments"][0]["likes"] == 51
    assert {comment["text"] for comment in block["raw_payload"]["comments"]}.isdisjoint({"评论0", "评论1"})


@pytest.mark.parametrize("compact", [False, True])
def test_douyin_top_comments_from_raw_payload_create_comment_block(tmp_path, compact):
    CardRepository(tmp_path).upsert_card(
        _card_payload(
            "card_douyin",
            platform="douyin",
            raw_payload={
                "top_comments": [
                    {"text": "想要教程", "likes": 35, "author": "甲"},
                    {"text": "这个工具不错", "digg_count": 12, "author": "乙"},
                ]
            },
        )
    )

    if compact:
        from backend.application.pipeline.information_card_builder import build_information_card, information_card_payload
        from backend.application.pipeline.normalize import normalize_raw_item
        from backend.domain.models.raw_item import RawItem
        from backend.infrastructure.storage.repositories import RawItemRepository

        card = CardRepository(tmp_path).get_card("card_douyin")
        raw = RawItem(raw_item_id="douyin-raw", connector_id="douyin", source_platform="douyin",
                      source_type="video", source_url=card["source_url"], captured_at=card["captured_at"],
                      raw_text=card["content"], raw_metadata=card["raw_payload"])
        payload = information_card_payload(build_information_card(normalize_raw_item(raw), raw))
        CardRepository(tmp_path).upsert_card({**payload, "card_id": "card_douyin"})
        RawItemRepository(tmp_path).upsert_raw_items([raw])

    created = enrich_platform_comments("card_douyin", tmp_path)

    assert created == 1
    block = CardContentBlockRepository(tmp_path).list_for_card("card_douyin")[0]
    assert block["status"] == "enriched"
    assert "想要教程" in block["body"]
    assert block["raw_payload"]["comments"][0]["likes"] == 35


def test_missing_platform_comment_support_records_blocked_block(tmp_path):
    CardRepository(tmp_path).upsert_card(_card_payload("card_scys", platform="scys"))

    created = enrich_platform_comments("card_scys", tmp_path)

    assert created == 1
    block = CardContentBlockRepository(tmp_path).list_for_card("card_scys")[0]
    assert block["block_kind"] == "platform_comments"
    assert block["status"] == "blocked"
    assert block["raw_payload"]["reason"] == "platform_comments_unavailable"
