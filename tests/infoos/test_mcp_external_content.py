"""MCP preserves long external document bodies; batch budgets prioritize text."""
import pytest
from fastapi.testclient import TestClient

from backend.api.mcp.evaluation_tools import _bounded_card
from backend.app.main import create_app
from backend.application.usecases.api_tokens import create_token
from backend.infrastructure.storage.repositories import CardContentBlockRepository
from tests.infoos.test_card_preview_api import _store_card
from tests.infoos.test_mcp_interface import _call


def test_single_external_content_order_and_bounded_batch(tmp_path):
    app = create_app(tmp_path)
    _store_card(tmp_path, card_id="external", platform="zsxq", title="帖子", author="作者")
    blocks = CardContentBlockRepository(tmp_path)
    for position, (layer, kind, body) in enumerate([
        ("derived", "ocr", "图" * 2000), ("original", "text", "帖子引言"),
        ("external", "external_doc", "全文" * 2500), ("external", "external_doc", "第二份文档"),
    ]):
        blocks.upsert_block({"block_id": f"block-{position}", "card_id": "external", "position": position,
            "layer": layer, "block_kind": kind, "status": "preserved", "body": body})
    token = create_token(tmp_path, name="reader", scopes=["intelligence:read"])["plaintext_token"]
    with TestClient(app) as client:
        single = _call(client, "tools/call", {"name": "get_card", "arguments": {"card_id": "external"}}, token).json()["result"]["structuredContent"]
        batch = _call(client, "tools/call", {"name": "get_cards", "arguments": {"card_ids": ["external"]}}, token).json()["result"]["structuredContent"]["items"][0]
        tools = _call(client, "tools/list", token=token).json()["result"]["tools"]
    assert single["content"] == "帖子引言"
    assert single["external_content"] == "全文" * 2500 + "\n\n第二份文档"
    assert "全文在 external_content" in next(t["description"] for t in tools if t["name"] == "get_card")
    assert batch["content"] == "帖子引言"
    assert len(batch["external_content"]) == 3996
    assert batch["full_length"] == len(single["content"]) + len(single["external_content"])
    assert batch["truncated"] is True and batch["truncated_hint"] == "调用 get_card 读取全文"
    assert batch["content_blocks"][0]["body"] == ""
    assert batch["content_blocks"][2]["body_in_external_content"] is True
    assert _budget(batch) == 4000


def _budget(card):
    return len(card["content"]) + len(card["external_content"]) + sum(len(b["body"]) for b in card["content_blocks"])


@pytest.mark.parametrize("original,external,derived", [("原文", "全文", "图" * 5000),
    ("原" * 5000, "全文", "图"), ("", "全" * 5000, "图"), ("原文", "全文", "图")])
def test_budget_never_counts_duplicate_primary_bodies(original, external, derived):
    blocks = [{"layer": "derived", "block_kind": "ocr", "segments": [], "body": derived},
              {"layer": "external", "block_kind": "external_doc", "segments": [], "body": external},
              {"layer": "original", "block_kind": "text", "segments": [], "body": original}]
    card = {"content": original, "external_content": external, "opportunity": None, "content_blocks": blocks}
    result = _bounded_card(card)
    assert result["full_length"] == len(original) + len(external)
    assert result["content"] == original[:4000]
    assert result["external_content"] == external[:4000 - len(result["content"])]
    assert _budget(result) <= 4000
    assert result["truncated"] is (_budget(card) - len(original) - len(external) > 4000)
    if result["truncated"]:
        assert result["truncated_hint"] == "调用 get_card 读取全文"
    else:
        assert "truncated_hint" not in result
    assert blocks[1]["body"] == external  # Input remains unchanged.
