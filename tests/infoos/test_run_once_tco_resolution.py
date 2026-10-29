"""run-once pipeline tests — t.co link resolution for X cards."""

import sys
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.workflows.run_once import run_once
from backend.infrastructure.storage.bootstrap import ensure_workbench_seeded
from backend.infrastructure.storage.repositories import RawItemRepository, SourceRepository
from tests.infoos._factories import make_supported_source_payload, opencli_command_startswith, cards_for_source


def _seed_x_list_source(tmp_path):
    ensure_workbench_seeded(tmp_path)
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(
        source_id="x_list_test",
        platform="x",
        source_type="list",
        identifier="9000000000000000001",
        connector_id="opencli_x_list",
    ))


def _run_async_content_enrichment_for_first_card(tmp_path) -> dict:
    from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task

    cards = cards_for_source(tmp_path, 'x_list_test')
    assert len(cards) == 1
    enrich_card_blocks_for_task(cards[0]["card_id"], tmp_path)
    return cards[0]


def test_async_content_enrichment_resolves_tco_links_for_linked_article(tmp_path, monkeypatch):
    def fake_opencli_run(command, **kwargs):
        from types import SimpleNamespace

        if opencli_command_startswith(command, "opencli", "twitter", "list-tweets"):
            payload = [
                {
                    "id": "9000000000000000002",
                    "url": "https://x.com/example_author/status/9000000000000000002",
                    "text": "看看这篇 https://t.co/example123 很有意思",
                    "created_at": "2026-04-24T09:00:00Z",
                    "author": {"name": "example_author", "username": "example_author"},
                }
            ]
            return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "article", "9000000000000000002"):
            return SimpleNamespace(stdout="", stderr="no article", returncode=1)
        if opencli_command_startswith(command, "opencli", "twitter", "thread", "9000000000000000002"):
            return SimpleNamespace(stdout="[]", stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "retweet", "9000000000000000002"):
            return SimpleNamespace(stdout="{}", stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "quote", "9000000000000000002"):
            return SimpleNamespace(stdout="{}", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli_run)

    class FakeHeadResponse:
        url = "https://example.com/article-page"

    def fake_head(url, allow_redirects=True, timeout=None):
        return FakeHeadResponse()

    class FakeGetResponse:
        text = """<html><body><article><p>这是一篇完整的文章正文，包含了详细的背景信息和数据。作者在文章中提出了三个主要观点，并对每个观点进行了深入的分析和论证。</p><p>文章指出，当前的技术发展趋势表明人工智能将在未来五年内对各行各业产生深远影响。</p><p>特别是在医疗、教育和金融领域，AI技术的应用已经开始改变传统的工作方式和商业模式。企业需要积极拥抱这些变化以保持竞争力。</p></article></body></html>"""

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=None):
        return FakeGetResponse()

    monkeypatch.setattr("backend.application.enrichment.x_blocks.tco.requests.head", fake_head)
    monkeypatch.setattr("backend.application.enrichment.x_blocks.tco.requests.get", fake_get)

    _seed_x_list_source(tmp_path)

    run_once(tmp_path)
    card = _run_async_content_enrichment_for_first_card(tmp_path)

    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])

    article_blocks = [b for b in blocks if b["block_kind"] == "linked_article"]
    assert len(article_blocks) == 1
    assert article_blocks[0]["status"] == "body_fetched"
    assert "完整的文章正文" in article_blocks[0]["body"]
    assert article_blocks[0]["source_url"] == "https://example.com/article-page"
    assert article_blocks[0]["layer"] == "context"
    assert article_blocks[0]["position"] == 5

    original_blocks = [b for b in blocks if b["block_kind"] == "original_tweet"]
    assert len(original_blocks) == 1
    assert "https://t.co/example123" in original_blocks[0]["body"]

def test_async_content_enrichment_marks_tco_resolved_to_x_url_as_unsupported(tmp_path, monkeypatch):
    """t.co that resolves to another X URL should get 'unsupported' status."""
    def fake_opencli_run(command, **kwargs):
        from types import SimpleNamespace

        if opencli_command_startswith(command, "opencli", "twitter", "list-tweets"):
            payload = [
                {
                    "id": "9000000000000000002",
                    "url": "https://x.com/example_author/status/9000000000000000002",
                    "text": "链接 https://t.co/example123 指向另一条推文",
                    "created_at": "2026-04-24T09:00:00Z",
                    "author": {"name": "example_author", "username": "example_author"},
                }
            ]
            return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "article", "9000000000000000002"):
            return SimpleNamespace(stdout="", stderr="no article", returncode=1)
        if opencli_command_startswith(command, "opencli", "twitter", "thread", "9000000000000000002"):
            return SimpleNamespace(stdout="[]", stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "retweet", "9000000000000000002"):
            return SimpleNamespace(stdout="{}", stderr="", returncode=0)
        if opencli_command_startswith(command, "opencli", "twitter", "quote", "9000000000000000002"):
            return SimpleNamespace(stdout="{}", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr("backend.infrastructure.connectors.opencli.subprocess.run", fake_opencli_run)
    monkeypatch.setattr("backend.application.enrichment.web_content.subprocess.run", fake_opencli_run)

    class FakeHeadResponse:
        url = "https://x.com/other_user/status/9000000000000000003"

    def fake_head(url, allow_redirects=True, timeout=None):
        return FakeHeadResponse()

    monkeypatch.setattr("backend.application.enrichment.x_blocks.tco.requests.head", fake_head)

    _seed_x_list_source(tmp_path)

    run_once(tmp_path)
    card = _run_async_content_enrichment_for_first_card(tmp_path)

    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    blocks = CardContentBlockRepository(tmp_path).list_for_card(card["card_id"])
    article_blocks = [b for b in blocks if b["block_kind"] == "linked_article"]
    assert len(article_blocks) == 1
    assert article_blocks[0]["status"] == "unsupported"
    assert "X 平台内容" in article_blocks[0]["provenance"]
