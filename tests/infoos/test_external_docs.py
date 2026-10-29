from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.enrichment.external_docs import enrich_external_docs
from backend.infrastructure.external_docs.feishu_browser import FeishuDocumentResult, FeishuMediaBlob
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


@pytest.fixture(autouse=True)
def _pending_feishu_fetch(monkeypatch):
    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(
            status="pending",
            source_url=url,
            reason="external_doc_browser_unavailable",
        ),
    )


def _scys_card(card_id: str, content: str) -> dict:
    return {
        "card_id": card_id,
        "source_id": None,
        "source_platform": "scys",
        "source_type": "article",
        "source_url": f"https://scys.com/articleDetail/xq_topic/{card_id}",
        "source_author": "author",
        "title": "生财帖子",
        "content": content,
        "summary": content[:80],
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-07-02T01:00:00Z",
        "captured_at": "2026-07-02T01:01:00Z",
        "processed_at": "2026-07-02T01:02:00Z",
        "raw_payload": {"topic_id": card_id, "normalized_content": content},
    }


def test_scys_feishu_url_creates_pending_external_doc_block(tmp_path):
    content = "完整内容在飞书文档：https://example.feishu.cn/docx/ABC123 ，请移步查看。"
    CardRepository(tmp_path).upsert_card(_scys_card("topic_with_url", content))

    created = enrich_external_docs("topic_with_url", tmp_path)

    assert created == 1
    block = CardContentBlockRepository(tmp_path).list_for_card("topic_with_url")[0]
    assert block["block_kind"] == "external_doc"
    assert block["status"] == "pending"
    assert block["source_url"] == "https://example.feishu.cn/docx/ABC123"
    assert block["raw_payload"]["provider"] == "feishu"
    assert block["raw_payload"]["detection"]["url_found"] is True


def test_scys_feishu_hint_without_url_creates_blocked_external_doc_block(tmp_path):
    content = "正文只放摘要，核心内容移步飞书好读版。感谢老师帮忙审稿。"
    CardRepository(tmp_path).upsert_card(_scys_card("topic_without_url", content))

    created = enrich_external_docs("topic_without_url", tmp_path)

    assert created == 1
    block = CardContentBlockRepository(tmp_path).list_for_card("topic_without_url")[0]
    assert block["block_kind"] == "external_doc"
    assert block["status"] == "blocked"
    assert block["raw_payload"]["block_reason"] == "external_doc_url_missing"
    assert block["raw_payload"]["detection"]["url_found"] is False


def test_scys_feishu_href_from_raw_metadata_creates_pending_external_doc_block(tmp_path):
    card = _scys_card("topic_with_hidden_href", "详细内容请移步飞书阅读。")
    card["raw_payload"] = {
        "topic_id": "topic_with_hidden_href",
        "normalized_content": card["content"],
        "raw_payload": {
            "links": ["https://example.feishu.cn/docx/HIDDEN123"],
            "html": '<a href="https://example.feishu.cn/docx/HIDDEN123">飞书阅读</a>',
        },
    }
    CardRepository(tmp_path).upsert_card(card)

    created = enrich_external_docs("topic_with_hidden_href", tmp_path)

    assert created == 1
    block = CardContentBlockRepository(tmp_path).list_for_card("topic_with_hidden_href")[0]
    assert block["status"] == "pending"
    assert block["source_url"] == "https://example.feishu.cn/docx/HIDDEN123"
    assert block["raw_payload"]["block_reason"] == "external_doc_browser_unavailable"
    assert block["raw_payload"]["next_action"] == "retry_with_authenticated_container_browser"


def test_scys_feishu_fetch_persists_visible_body_and_media(tmp_path, monkeypatch):
    content = "完整内容在飞书文档：https://example.feishu.cn/docx/FETCHED"
    CardRepository(tmp_path).upsert_card(_scys_card("topic_fetched", content))
    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(
            status="fetched",
            complete=True,
            source_url=url,
            body="这是通过容器内浏览器取得的完整可见正文。" * 5,
            images=["https://cdn.example.com/inline-image"],
            videos=["https://cdn.example.com/inline-video"],
            media_blobs=[
                FeishuMediaBlob(
                    url="https://internal-api-drive-stream.feishu.cn/inline/image-token",
                    kind="image",
                    mime_type="image/png",
                    content=b"\x89PNG\r\n\x1a\ninline-image",
                )
            ],
            diagnostics={"body_chars": 95},
        ),
    )

    assert enrich_external_docs("topic_fetched", tmp_path) == 1

    blocks = CardContentBlockRepository(tmp_path).list_for_card("topic_fetched")
    external_block = next(block for block in blocks if block["block_kind"] == "external_doc")
    media_block = next(block for block in blocks if block["block_kind"] == "image")
    assert external_block["status"] == "body_fetched"
    assert external_block["body"].startswith("这是通过容器内浏览器取得的完整可见正文")
    assert external_block["raw_payload"]["fetch_state"] == "fetched"
    assert external_block["raw_payload"]["images"] == ["https://cdn.example.com/inline-image"]
    assert external_block["raw_payload"]["videos"] == ["https://cdn.example.com/inline-video"]
    assert external_block["raw_payload"]["document_url"] == "https://example.feishu.cn/docx/FETCHED"
    assets = CardAssetRepository(tmp_path).list_for_card("topic_fetched")
    assert len(assets) == 1
    assert assets[0]["status"] == "downloaded"
    assert assets[0]["raw_payload"]["strategy"] == "feishu_authenticated_browser"
    assert media_block["raw_payload"]["asset_id"] == assets[0]["asset_id"]
    stored = tmp_path / "data" / "assets" / str(assets[0]["local_path"])
    assert stored.read_bytes() == b"\x89PNG\r\n\x1a\ninline-image"

    assert enrich_external_docs("topic_fetched", tmp_path) == 1
    repeated = CardContentBlockRepository(tmp_path).list_for_card("topic_fetched")
    assert len(repeated) == 2
    assert next(block for block in repeated if block["block_kind"] == "image")["position"] == media_block["position"]


def test_external_doc_upsert_preserves_position_and_increments_retry_version(tmp_path):
    content = "完整内容在飞书文档：https://example.feishu.cn/docx/RETRY"
    CardRepository(tmp_path).upsert_card(_scys_card("topic_retry", content))

    assert enrich_external_docs("topic_retry", tmp_path) == 1
    first = CardContentBlockRepository(tmp_path).list_for_card("topic_retry")[0]
    assert first["position"] == 0
    assert first["raw_payload"]["retry_count"] == 1

    assert enrich_external_docs("topic_retry", tmp_path) == 1
    blocks = CardContentBlockRepository(tmp_path).list_for_card("topic_retry")
    assert len(blocks) == 1
    assert blocks[0]["position"] == 0
    assert blocks[0]["raw_payload"]["retry_count"] == 2


def test_fetched_external_doc_without_authenticated_media_tracks_repair_attempts(
    tmp_path, monkeypatch
):
    content = "完整内容在飞书文档：https://example.feishu.cn/docx/MEDIA-RETRY"
    CardRepository(tmp_path).upsert_card(_scys_card("topic_media_retry", content))
    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: FeishuDocumentResult(
            status="fetched",
            complete=True,
            source_url=url,
            body="正文已经取得，但登录态媒体仍未返回。" * 5,
            images=["https://internal-api-drive-stream.feishu.cn/image/1"],
            media_blobs=[],
            diagnostics={"body_chars": 85, "authenticated_media_count": 0},
        ),
    )

    assert enrich_external_docs("topic_media_retry", tmp_path) == 1
    assert enrich_external_docs("topic_media_retry", tmp_path) == 1

    block = CardContentBlockRepository(tmp_path).list_for_card("topic_media_retry")[0]
    assert block["status"] == "body_fetched"
    assert block["raw_payload"]["retry_count"] == 0
    assert block["raw_payload"]["authenticated_media_retry_count"] == 2


def test_external_doc_retry_reuses_persisted_feishu_url_when_card_no_longer_exposes_it(
    tmp_path, monkeypatch
):
    card_id = "topic_persisted_url_retry"
    original_url = "https://example.feishu.cn/docx/PERSISTED"
    CardRepository(tmp_path).upsert_card(
        _scys_card(card_id, f"完整内容在飞书文档：{original_url}")
    )
    assert enrich_external_docs(card_id, tmp_path) == 1

    CardRepository(tmp_path).upsert_card(
        _scys_card(card_id, "完整内容仍在飞书文档中，但当前卡片正文不再暴露链接。")
    )
    fetched_urls: list[str] = []
    monkeypatch.setattr(
        "backend.application.enrichment.external_docs.fetch_feishu_document",
        lambda url: (
            fetched_urls.append(url)
            or FeishuDocumentResult(
                status="fetched",
            complete=True,
                source_url=url,
                body="通过已持久化的安全飞书链接恢复得到的完整正文。" * 4,
            )
        ),
    )

    assert enrich_external_docs(card_id, tmp_path) == 1

    assert fetched_urls == [original_url]
    block = CardContentBlockRepository(tmp_path).list_for_card(card_id)[0]
    assert block["status"] == "body_fetched"
    assert block["raw_payload"]["document_url"] == original_url


def test_non_scys_card_without_external_doc_hint_is_ignored(tmp_path):
    CardRepository(tmp_path).upsert_card(
        _scys_card("normal_topic", "这里是一篇普通生财正文，没有外部文档提示。")
        | {"source_platform": "xiaohongshu"}
    )

    assert enrich_external_docs("normal_topic", tmp_path) == 0
