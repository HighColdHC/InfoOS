from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.card_preview import router
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.storage.bootstrap import initialize_runtime_storage
from backend.infrastructure.storage.raw_items import RawItemRepository
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.repositories import (
    CardAssetRepository,
    CardContentBlockRepository,
    CardRepository,
    SourceRepository,
)
from tests.infoos._factories import make_source_payload


NOW = "2026-07-16T10:00:00Z"


def _client(base_dir) -> TestClient:
    initialize_runtime_storage(base_dir)
    app = FastAPI()
    app.state.base_dir = base_dir
    app.include_router(router)
    return TestClient(app)


def _store_card(
    base_dir,
    *,
    card_id: str,
    platform: str,
    title: str,
    author: str,
    completeness: str = "complete",
    updated_at: str = NOW,
    raw_payload: dict | None = None,
) -> None:
    source_id = f"source-{platform}"
    if SourceRepository(base_dir).get_source(source_id) is None:
        SourceRepository(base_dir).upsert_source(
            make_source_payload(
                source_id=source_id,
                connector_id=source_id,
                platform=platform,
                display_name=f"{platform.upper()} 观察源",
            )
        )
    CardRepository(base_dir).upsert_card(
        {
            "card_id": card_id,
            "card_type": "information",
            "version": 2,
            "content_hash": f"sha256:{card_id:0<64}"[:71],
            "updated_at": updated_at,
            "status": "active",
            "completeness_status": completeness,
            "missing_reasons": [] if completeness == "complete" else ["video_transcript_missing"],
            "processor_version": "collector-v1",
            "raw_connector_id": source_id,
            "raw_item_id": f"raw-{card_id}",
            "source_run_id": f"run-{card_id}",
            "source_id": source_id,
            "source_platform": platform,
            "source_type": "account",
            "source_url": f"https://example.test/{platform}/{card_id}",
            "source_author": author,
            "title": title,
            "content": f"{title} 的卡片正文",
            "published_at": NOW,
            "captured_at": NOW,
            "processed_at": NOW,
            "raw_payload": raw_payload or {},
        }
    )


def _set_card_publication(base_dir, card_id, published_at):
    repo = CardRepository(base_dir)
    repo.upsert_card({**repo.get_card(card_id), "published_at": published_at})


def test_card_preview_list_filters_finalized_cards_and_searches_readable_blocks(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-x",
        platform="x",
        title="产品发布",
        author="Alice",
        completeness="partial",
        updated_at="2026-07-16T12:00:00Z",
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "body-x",
            "card_id": "card-x",
            "position": 0,
            "layer": "original",
            "block_kind": "tweet",
            "status": "preserved",
            "body": "# 产品发布\n- [x] **独有检索词**：[海边信号](https://example.test/item)\nhttps://example.test/raw",
        }
    )
    _store_card(
        tmp_path,
        card_id="card-youtube",
        platform="youtube",
        title="视频更新",
        author="Bob",
        updated_at="2026-07-16T11:00:00Z",
    )
    _store_card(
        tmp_path,
        card_id="card-processing",
        platform="x",
        title="尚未终结",
        author="Carol",
    )
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            "update cards set status = 'processing', version = 0, content_hash = null where card_id = ?",
            ("card-processing",),
        )

    response = client.get(
        "/api/card-preview",
        params={"query": "海边信号", "platform": "x", "completeness": "partial", "limit": 1, "offset": 0},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "card_id": "card-x",
                "title": "产品发布 的卡片正文",
                "author": "Alice",
                "platform": "x",
                "is_digest": None,
                "published_at": NOW,
                "first_seen_at": None,
                "completeness_status": "partial",
                "attachment_kinds": [],
                "version": 2,
                "updated_at": "2026-07-16T12:00:00Z",
                "source_label": "X 观察源",
                "source_url": "https://example.test/x/card-x",
                "excerpt": "独有检索词：海边信号",
                "summary_excerpt": None,
                "cover_asset_url": None,
                "cover_kind": None,
                "media_count": 0,
                "evaluation": None,
                "opportunity": None,
            }
        ],
        "total": 1,
        "processing_total": 1,
        "limit": 1,
        "offset": 0,
        "sort": "published_desc",
    }
    for query in (
        "产品发布",
        "Alice",
        "海边信号",
        "https://example.test/x/card-x",
    ):
        matching = client.get("/api/card-preview", params={"query": query})
        assert matching.status_code == 200
        assert [item["card_id"] for item in matching.json()["items"]] == ["card-x"]

    second_page = client.get("/api/card-preview", params={"limit": 1, "offset": 1, "sort": "updated_desc"})
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 2
    assert [item["card_id"] for item in second_page.json()["items"]] == ["card-youtube"]
    assert second_page.json()["sort"] == "updated_desc"


def test_card_preview_published_sort_uses_first_seen_for_missing_publication(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(tmp_path, card_id="early", platform="x", title="较早发布", author="甲", updated_at="2026-07-19T00:00:00Z")
    _store_card(tmp_path, card_id="seen", platform="x", title="首次入库", author="乙", updated_at="2026-07-18T00:00:00Z")
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("""insert into raw_items (connector_id, raw_item_id, source_platform, source_type,
            source_url, captured_at, first_seen_at, last_seen_at) values (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("source-x", "raw-seen", "x", "account", "https://example.test/seen",
             NOW, "2026-07-17T00:00:00Z", NOW))
    _set_card_publication(tmp_path, "early", "2026-07-15T00:00:00Z")
    _set_card_publication(tmp_path, "seen", None)
    published = client.get("/api/card-preview").json()
    updated = client.get("/api/card-preview", params={"sort": "updated_desc"}).json()
    assert [item["card_id"] for item in published["items"]] == ["seen", "early"]
    assert published["items"][0]["first_seen_at"] == "2026-07-17T00:00:00Z"
    assert [item["card_id"] for item in updated["items"]] == ["early", "seen"]


def test_card_preview_published_sort_normalizes_mixed_timestamps(tmp_path) -> None:
    client = _client(tmp_path)
    publication_times = {
        "unix": "1784729776",  # 2026-07-22T14:16:16Z
        "twitter": "Tue Jul 21 20:13:46 +0000 2026",
        "offset": "2026-07-24T00:11:20-07:00",
        "relative": "pirms 7 stundām",
    }
    for card_id in publication_times:
        _store_card(tmp_path, card_id=card_id, platform="youtube", title=card_id, author="作者")
    with CardRepository(tmp_path).connect() as conn:
        conn.execute("""insert into raw_items (connector_id, raw_item_id, source_platform, source_type,
            source_url, captured_at, first_seen_at, last_seen_at) values (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("source-youtube", "raw-relative", "youtube", "account", "https://example.test/relative",
             NOW, "2026-07-23T00:00:00Z", NOW))

    for card_id, published_at in publication_times.items():
        _set_card_publication(tmp_path, card_id, published_at)
    response = client.get("/api/card-preview", params={"sort": "published_desc"})
    assert response.status_code == 200
    assert [item["card_id"] for item in response.json()["items"]] == [
        "offset", "relative", "unix", "twitter",
    ]
    assert response.json()["items"][1]["first_seen_at"] == "2026-07-23T00:00:00Z"


def test_card_preview_processing_filter_paginates_and_never_reports_complete(tmp_path) -> None:
    client = _client(tmp_path)
    for index in range(3):
        card_id = f"card-processing-{index}"
        _store_card(
            tmp_path,
            card_id=card_id,
            platform="x",
            title=f"处理中 {index}",
            author="Worker",
            completeness="complete",
            updated_at=f"2026-07-16T1{index}:00:00Z",
        )
        with CardRepository(tmp_path).connect() as conn:
            conn.execute(
                "update cards set status='processing', version=0, content_hash=null where card_id=?",
                (card_id,),
            )
    _store_card(tmp_path, card_id="card-active", platform="x", title="已完成", author="Worker")

    response = client.get(
        "/api/card-preview",
        params={"status": "processing", "limit": 1, "offset": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["processing_total"] == 3
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert len(payload["items"]) == 1
    assert payload["items"][0]["completeness_status"] == "processing"
    assert client.get("/api/card-preview").json()["total"] == 1
    # The badge's old request only supplied status=processing and limit=1.
    badge_total = client.get("/api/card-preview?status=processing&limit=1").json()["total"]
    for filters in (
        {"query": "无匹配", "platform": "youtube", "source_id": "missing", "offset": 10,
         "published_since": "2020-01-01", "published_until": "2020-01-02", "completeness": "partial"},
        {"recommended": "1", "status": "processing"},
        {"evaluation": "unevaluated", "digest": "1"},
    ):
        filtered = client.get("/api/card-preview", params=filters).json()
        assert filtered["total"] == 0
        assert filtered["processing_total"] == badge_total == 3


def test_card_preview_rejects_unknown_status_filter(tmp_path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/card-preview", params={"status": "complete"})

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_status"


def test_card_preview_detail_groups_body_images_and_videos_with_trace(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-detail",
        platform="xiaohongshu",
        title="城市散步",
        author="漫游者",
        completeness="partial",
    )
    blocks = CardContentBlockRepository(tmp_path)
    blocks.upsert_block(
        {
            "block_id": "comments-last",
            "card_id": "card-detail",
            "position": 4,
            "layer": "context",
            "block_kind": "platform_comments",
            "status": "enriched",
            "body": "1. 读者: 路线很好 (likes: 8)",
            "raw_payload": {"comments": [{"author": "读者", "text": "路线很好", "likes": 8}]},
        }
    )
    blocks.upsert_block(
        {
            "block_id": "body-later",
            "card_id": "card-detail",
            "position": 2,
            "layer": "derived",
            "block_kind": "transcript",
            "status": "enriched",
            "body": "第二段正文",
            "segments": [{"start": 0, "end": 2, "text": "第二段正文"}],
            "provenance": "video transcription",
        }
    )
    blocks.upsert_block(
        {
            "block_id": "body-first",
            "card_id": "card-detail",
            "position": 1,
            "layer": "original",
            "block_kind": "card_content",
            "status": "preserved",
            "body": "第一段正文",
            "source_url": "https://example.test/body",
        }
    )
    blocks.upsert_block({
        "block_id": "ai-summary", "card_id": "card-detail", "position": 5,
        "layer": "intelligence", "block_kind": "summary", "status": "enriched",
        "body": "**中文** [解读](https://example.test/summary)\n> 内容", "raw_payload": {"purpose": "ai_interpretation"},
    })
    blocks.upsert_block(
        {
            "block_id": "machine-gap",
            "card_id": "card-detail",
            "position": 3,
            "layer": "derived",
            "block_kind": "media_download_gap",
            "status": "failed",
            "body": "内部失败上下文",
        }
    )
    assets = CardAssetRepository(tmp_path)
    media_root = tmp_path / "data" / "assets" / "card-detail"
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "image.jpg").write_bytes(b"image")
    (media_root / "video.mp4").write_bytes(b"video")
    assets.upsert_asset(
        {
            "asset_id": "image-1",
            "card_id": "card-detail",
            "asset_kind": "image",
            "source_url": "https://cdn.test/image.jpg",
            "local_path": "card-detail/image.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 101,
            "content_hash": "sha256:image",
            "status": "downloaded",
        }
    )
    assets.upsert_asset(
        {
            "asset_id": "video-1",
            "card_id": "card-detail",
            "asset_kind": "video",
            "source_url": "https://cdn.test/video.mp4",
            "local_path": "card-detail/video.mp4",
            "mime_type": "video/mp4",
            "size_bytes": 202,
            "content_hash": "sha256:video",
            "status": "downloaded",
        }
    )
    assets.upsert_asset(
        {
            "asset_id": "audio-1",
            "card_id": "card-detail",
            "asset_kind": "audio",
            "source_url": "https://cdn.test/audio.m4a",
            "local_path": "card-detail/audio.m4a",
            "mime_type": "audio/mp4",
            "size_bytes": 303,
            "status": "downloaded",
        }
    )

    response = client.get("/api/card-preview/card-detail")

    assert response.status_code == 200
    payload = response.json()
    assert payload["card"] == {
        "card_id": "card-detail",
        "title": "城市散步",
        "author": "漫游者",
        "platform": "xiaohongshu",
        "is_digest": None,
        "published_at": NOW,
        "captured_at": None,
        "first_seen_at": None,
        "updated_at": NOW,
        "status": "active",
        "completeness_status": "partial",
        "version": 2,
        "content_hash": "sha256:card-detail00000000000000000000000000000000000000000000000000000",
        "processor_version": "collector-v1",
        "source_label": "XIAOHONGSHU 观察源",
        "source_url": "https://example.test/xiaohongshu/card-detail",
        "raw_item_id": "raw-card-detail",
        "source_run_id": "run-card-detail",
        "attachment_count": 2,
        "engagement_metrics": {},
    }
    assert [block["block_id"] for block in payload["body_blocks"]] == ["body-first", "body-later", "ai-summary"]
    assert [block["block_id"] for block in payload["comment_blocks"]] == ["comments-last"]
    assert payload["comment_blocks"][0]["raw_payload"]["comments"][0]["text"] == "路线很好"
    assert payload["body_blocks"][0]["content"] == "第一段正文"
    assert payload["body_blocks"][0]["sort_order"] == 1
    assert [asset["asset_id"] for asset in payload["image_assets"]] == ["image-1"]
    assert [asset["asset_id"] for asset in payload["video_assets"]] == ["video-1"]
    assert payload["image_assets"][0]["asset_url"] == "/api/card-preview/assets/image-1"
    list_item = client.get("/api/card-preview", params={"query": "城市散步"}).json()["items"][0]
    assert list_item["excerpt"] == "第一段正文"
    assert list_item["summary_excerpt"] == "中文 解读 内容"
    assert list_item["cover_asset_url"] == payload["image_assets"][0]["asset_url"]
    assert list_item["cover_kind"] == "image"
    assert list_item["media_count"] == 2
    (media_root / "image.jpg").unlink()
    video_only = client.get("/api/card-preview", params={"query": "城市散步"}).json()["items"][0]
    assert video_only["cover_asset_url"] == payload["video_assets"][0]["asset_url"]
    assert video_only["cover_kind"] == "video"
    assert video_only["media_count"] == 1
    assert payload["missing_reasons"] == ["video_transcript_missing"]
    assert payload["trace"] == {
        "raw_connector_id": "source-xiaohongshu",
        "raw_item_id": "raw-card-detail",
        "source_run_id": "run-card-detail",
    }
    assert "insight" not in payload
    assert "topic" not in payload
    assert "decision" not in payload


@pytest.mark.parametrize("gap_status", ["blocked", "resolved", None])
def test_card_preview_detail_projects_only_unresolved_media_gap(tmp_path, gap_status) -> None:
    client = _client(tmp_path)
    _store_card(tmp_path, card_id="card-gap", platform="douyin", title="视频", author="作者")
    if gap_status:
        CardContentBlockRepository(tmp_path).upsert_block({
            "block_id": "gap", "card_id": "card-gap", "position": 1,
            "layer": "media", "block_kind": "media_download_gap", "status": gap_status,
            "body": "internal stderr", "raw_payload": {
                "reason": "douyin_browser_no_media_resources", "details": {"stderr": "internal error"},
            },
        })
    response = client.get("/api/card-preview/card-gap")
    assert response.status_code == 200
    assert response.json()["media_gap"] == (
        {"reason": "douyin_browser_no_media_resources", "status": "blocked"}
        if gap_status == "blocked" else None
    )
    assert all(block["block_kind"] != "media_download_gap" for block in response.json()["body_blocks"])
    assert "internal" not in response.text


def test_card_preview_detail_chooses_gap_by_update_time_after_filtering_resolved(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(tmp_path, card_id="card-gap", platform="douyin", title="视频", author="作者")
    blocks = CardContentBlockRepository(tmp_path)
    for block_id, position, status, updated_at in [
        ("recent", 0, "blocked", "2026-07-17T10:00:00Z"),
        ("old", 9, "blocked", "2026-07-16T10:00:00Z"),
        ("resolved", 10, "resolved", "2026-07-18T10:00:00Z"),
    ]:
        blocks.upsert_block({
            "block_id": block_id, "card_id": "card-gap", "position": position,
            "layer": "media", "block_kind": "media_download_gap", "status": status,
            "raw_payload": {"reason": block_id, "details": {}},
        })
        with blocks.connect() as conn:
            conn.execute("update card_content_blocks set updated_at=? where block_id=?", (updated_at, block_id))
    response = client.get("/api/card-preview/card-gap")
    assert response.status_code == 200
    assert response.json()["media_gap"] == {"reason": "recent", "status": "blocked"}


def test_card_preview_detail_projects_engagement_metrics_from_card_payload(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-x-metrics",
        platform="x",
        title="互动数据",
        author="Metric Author",
        raw_payload={
            "metrics": {
                "views": "1,234",
                "likes": 0,
                "replies": 8,
                "retweets": -1,
                "bookmarks": "not-a-number",
                "quotes": 3,
            }
        },
    )

    response = client.get("/api/card-preview/card-x-metrics")

    assert response.status_code == 200
    assert response.json()["card"]["engagement_metrics"] == {
        "view_count": 1234,
        "like_count": 0,
        "reply_count": 8,
        "quote_count": 3,
    }


def test_card_preview_detail_reads_current_raw_counters_without_rebuilding_card(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path, card_id="card-x-live", platform="x", title="计数更新",
        author="Author", raw_payload={"metrics": {"likes": 1}},
    )
    repo = RawItemRepository(tmp_path)
    raw = RawItem(
        raw_item_id="raw-card-x-live", connector_id="source-x",
        source_platform="x", source_type="social_post",
        source_url="https://example.test/x/card-x-live", captured_at=NOW,
        raw_text="计数更新", raw_metadata={"metrics": {"likes": 1}},
    )
    repo.upsert_raw_items([raw])
    before = client.get("/api/card-preview/card-x-live").json()["card"]
    raw.raw_metadata["metrics"]["likes"] = 9
    assert repo.upsert_raw_items_with_changes([raw]) == []
    after = client.get("/api/card-preview/card-x-live").json()["card"]
    assert before["engagement_metrics"] == {"like_count": 1}
    assert after["engagement_metrics"] == {"like_count": 9}
    assert (after["version"], after["content_hash"]) == (before["version"], before["content_hash"])
    assert CardRepository(tmp_path).get_card("card-x-live")["raw_payload"]["metrics"]["likes"] == 1


def test_card_preview_detail_ignores_retired_ai_processing(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(tmp_path, card_id="legacy-ai", platform="rss", title="English source", author="Author")
    source_repo = SourceRepository(tmp_path)
    source = source_repo.get_source("source-rss")
    source["raw_metadata"]["ai_processing"] = {"enabled": True}
    source_repo.upsert_source(source)
    TaskRepository.for_base_dir(tmp_path).enqueue(Task(
        task_id="retired-ai", task_type=TaskType.CARD_LLM_ENRICH,
        target_kind="card", target_id="legacy-ai", status=TaskStatus.SKIPPED,
        input_version="legacy", error_reason="feature_retired",
    ))
    for position, kind in enumerate(("summary", "translation")):
        CardContentBlockRepository(tmp_path).upsert_block({
            "block_id": f"legacy-{kind}", "card_id": "legacy-ai", "position": position,
            "layer": "intelligence", "block_kind": kind, "status": "enriched",
            "body": f"历史{kind}",
        })
    payload = client.get("/api/card-preview/legacy-ai").json()
    assert "intelligence_status" not in payload
    assert payload["evaluation"] is None
    assert {block["block_kind"] for block in payload["body_blocks"]} >= {"summary", "translation"}


def test_card_preview_detail_excludes_media_that_cannot_be_opened(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-stale-media",
        platform="x",
        title="附件已清理",
        author="Cleaner",
    )
    CardAssetRepository(tmp_path).upsert_asset(
        {
            "asset_id": "stale-image",
            "card_id": "card-stale-media",
            "asset_kind": "image",
            "source_url": "https://cdn.test/stale.jpg",
            "local_path": "card-stale-media/stale.jpg",
            "mime_type": "image/jpeg",
            "status": "downloaded",
        }
    )

    response = client.get("/api/card-preview/card-stale-media")

    assert response.status_code == 200
    assert response.json()["image_assets"] == []
    assert response.json()["card"]["attachment_count"] == 0
    list_item = client.get("/api/card-preview", params={"query": "附件已清理"}).json()["items"][0]
    assert list_item["cover_asset_url"] is None
    assert list_item["media_count"] == 0


def test_card_preview_detail_uses_stable_card_content_when_no_body_blocks_exist(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-fallback",
        platform="reddit",
        title="社区讨论",
        author="Dana",
    )

    response = client.get("/api/card-preview/card-fallback")

    assert response.status_code == 200
    assert response.json()["body_blocks"] == [
        {
            "block_id": "fallback-card-fallback-content",
            "card_id": "card-fallback",
            "layer": "original",
            "block_kind": "card_content",
            "content": "社区讨论 的卡片正文",
            "body": "社区讨论 的卡片正文",
            "source_url": "https://example.test/reddit/card-fallback",
            "status": "preserved",
            "sort_order": 0,
            "provenance": "card content fallback",
            "raw_payload": {},
        }
    ]
    assert response.json()["comment_blocks"] == []


def test_card_preview_detail_keeps_original_content_when_transcript_exists(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-xhs-transcript",
        platform="xiaohongshu",
        title="视频帖子",
        author="作者",
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "generic-platform-footer",
            "card_id": "card-xhs-transcript",
            "position": 1,
            "layer": "context",
            "block_kind": "linked_article",
            "status": "body_fetched",
            "body": "沪ICP备号 营业执照 加载中",
            "provenance": "Generic article fetch via async enrichment",
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "transcript-card-xhs-transcript",
            "card_id": "card-xhs-transcript",
            "position": 3,
            "layer": "derived",
            "block_kind": "video_transcript",
            "status": "enriched",
            "body": "视频转录正文",
        }
    )

    response = client.get("/api/card-preview/card-xhs-transcript")

    assert response.status_code == 200
    assert [block["block_id"] for block in response.json()["body_blocks"]] == [
        "fallback-card-xhs-transcript-content",
        "transcript-card-xhs-transcript",
    ]
    assert response.json()["body_blocks"][0]["content"] == "视频帖子 的卡片正文"


def test_card_preview_detail_returns_frozen_404_for_missing_or_unfinalized_cards(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-processing-detail",
        platform="douyin",
        title="仍在处理",
        author="Eve",
    )
    with CardRepository(tmp_path).connect() as conn:
        conn.execute(
            "update cards set status = 'processing', version = 0, content_hash = null where card_id = ?",
            ("card-processing-detail",),
        )

    missing = client.get("/api/card-preview/does-not-exist")
    processing = client.get("/api/card-preview/card-processing-detail")

    assert missing.status_code == 404
    assert missing.json() == {"code": "card_not_found", "message": "Card not found"}
    assert processing.status_code == 404
    assert processing.json() == {"code": "card_not_found", "message": "Card not found"}


def test_card_preview_asset_serves_repository_owned_file_by_asset_id(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-media",
        platform="youtube",
        title="带图卡片",
        author="Frank",
    )
    asset_path = tmp_path / "data" / "assets" / "card-media" / "cover.jpg"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(b"jpeg-fixture")
    assets = CardAssetRepository(tmp_path)
    assets.upsert_asset(
        {
            "asset_id": "asset-cover",
            "card_id": "card-media",
            "asset_kind": "image",
            "source_url": "https://cdn.test/cover.jpg",
            "local_path": "card-media/cover.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 12,
            "status": "downloaded",
        }
    )
    unsafe_path = tmp_path / "data" / "assets" / "card-media" / "payload.html"
    unsafe_path.write_text("<script>bad()</script>", encoding="utf-8")
    assets.upsert_asset(
        {
            "asset_id": "asset-unsafe-mime",
            "card_id": "card-media",
            "asset_kind": "image",
            "source_url": "https://cdn.test/payload.html",
            "local_path": "card-media/payload.html",
            "mime_type": "text/html",
            "size_bytes": unsafe_path.stat().st_size,
            "status": "downloaded",
        }
    )

    response = client.get("/api/card-preview/assets/asset-cover")
    unsafe_mime = client.get("/api/card-preview/assets/asset-unsafe-mime")
    ranged = client.get(
        "/api/card-preview/assets/asset-cover",
        headers={"Range": "bytes=5-9"},
    )

    assert response.status_code == 200
    assert response.content == b"jpeg-fixture"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["cache-control"] == "private, max-age=604800"
    assert unsafe_mime.status_code == 200
    assert unsafe_mime.headers["content-type"] == "application/octet-stream"
    assert ranged.status_code == 206
    assert ranged.content == b"fixtu"
    assert ranged.headers["content-range"] == "bytes 5-9/12"
    assert ranged.headers["cache-control"] == "private, max-age=604800"


def test_card_preview_asset_rejects_unknown_missing_and_out_of_root_files(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(
        tmp_path,
        card_id="card-unsafe",
        platform="x",
        title="附件边界",
        author="Grace",
    )
    (tmp_path / "data" / "secret.txt").write_text("must not escape", encoding="utf-8")
    assets = CardAssetRepository(tmp_path)
    for asset_id, local_path in (
        ("asset-outside", "../secret.txt"),
        ("asset-missing-file", "card-unsafe/missing.jpg"),
    ):
        assets.upsert_asset(
            {
                "asset_id": asset_id,
                "card_id": "card-unsafe",
                "asset_kind": "image",
                "source_url": f"https://cdn.test/{asset_id}.jpg",
                "local_path": local_path,
                "mime_type": "image/jpeg",
                "status": "downloaded",
            }
        )
    cleaned_path = tmp_path / "data" / "assets" / "card-unsafe" / "cleaned.jpg"
    cleaned_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned_path.write_bytes(b"stale-file")
    assets.upsert_asset(
        {
            "asset_id": "asset-cleaned",
            "card_id": "card-unsafe",
            "asset_kind": "image",
            "source_url": "https://cdn.test/asset-cleaned.jpg",
            "local_path": "card-unsafe/cleaned.jpg",
            "mime_type": "image/jpeg",
            "status": "downloaded",
            "cleanup_state": "cleaned",
        }
    )

    unknown = client.get("/api/card-preview/assets/no-such-asset")
    outside = client.get("/api/card-preview/assets/asset-outside")
    missing_file = client.get("/api/card-preview/assets/asset-missing-file")
    cleaned = client.get("/api/card-preview/assets/asset-cleaned")

    assert unknown.status_code == 404
    assert "cache-control" not in unknown.headers
    assert unknown.json() == {"code": "asset_not_found", "message": "Asset not found"}
    assert outside.status_code == 403
    assert outside.json() == {
        "code": "asset_path_forbidden",
        "message": "Asset path is outside the managed assets directory",
    }
    assert missing_file.status_code == 404
    assert missing_file.json() == {
        "code": "asset_file_not_found",
        "message": "Asset file not found",
    }
    assert cleaned.status_code == 404
    assert cleaned.json() == {"code": "asset_not_found", "message": "Asset not found"}


def test_card_preview_list_returns_stable_validation_errors(tmp_path) -> None:
    client = _client(tmp_path)

    invalid_limit = client.get("/api/card-preview", params={"limit": 0})
    invalid_offset = client.get("/api/card-preview", params={"offset": -1})
    invalid_completeness = client.get(
        "/api/card-preview",
        params={"completeness": "processing"},
    )
    invalid_sort = client.get("/api/card-preview", params={"sort": "random"})

    assert invalid_limit.status_code == 422
    assert invalid_limit.json() == {
        "code": "invalid_pagination",
        "message": "limit must be an integer between 1 and 100",
    }
    assert invalid_offset.status_code == 422
    assert invalid_offset.json() == {
        "code": "invalid_pagination",
        "message": "offset must be a non-negative integer",
    }
    assert invalid_completeness.status_code == 422
    assert invalid_completeness.json() == {
        "code": "invalid_completeness",
        "message": "completeness must be complete or partial",
    }
    assert invalid_sort.status_code == 422
    assert invalid_sort.json() == {"code": "invalid_sort", "message": "sort must be published_desc or updated_desc"}


def test_card_preview_search_requires_every_term_but_not_as_a_phrase(tmp_path) -> None:
    client = _client(tmp_path)
    _store_card(tmp_path, card_id="card-a", platform="x", title="短视频脚本结构", author="Alice",
                updated_at="2026-07-16T12:00:00Z")
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "body-a", "card_id": "card-a", "position": 0, "layer": "original",
        "block_kind": "tweet", "status": "preserved", "body": "Claude Code 做剪辑自动化",
    })
    _store_card(tmp_path, card_id="card-b", platform="x", title="短视频选题", author="Bob",
                updated_at="2026-07-16T11:00:00Z")

    def ids(query: str) -> list[str]:
        payload = client.get("/api/card-preview", params={"query": query}).json()
        assert payload["total"] == len(payload["items"])
        return [item["card_id"] for item in payload["items"]]

    assert ids("短视频 剪辑") == ["card-a"]          # title term + block term
    assert ids("  CLAUDE   短视频 ") == ["card-a"]    # case and spacing do not matter
    assert ids("短视频") == ["card-b", "card-a"]        # same publish time: card_id desc
    assert ids("短视频 不存在的词") == []


@pytest.fixture
def evaluated_preview(tmp_path):
    client = _client(tmp_path)
    for card_id, platform, completeness in (
        ("recommended-high", "x", "partial"), ("recommended-low", "x", "complete"),
        ("recommended-quality", "x", "partial"), ("recommended-newer", "x", "partial"),
        ("recommended-null", "youtube", "complete"), ("rejected", "x", "complete"),
        ("unevaluated", "x", "complete"), ("processing", "x", "complete"),
    ):
        _store_card(tmp_path, card_id=card_id, platform=platform, title="关注产品 " + card_id,
                    author="观察者", completeness=completeness)
    with CardRepository(tmp_path).connect() as conn:
        InterestProfileRepository.insert_current(
            conn, markdown="关注产品", sections={}, content_hash="profile", source="test", created_at=NOW,
        )
        conn.execute("update cards set status='processing', version=0, content_hash=null where card_id='processing'")
    _set_card_publication(tmp_path, "recommended-newer", "2026-07-17T10:00:00Z")
    for card_id, quality, relevance, passed in (
        ("recommended-high", 90, 90, True), ("recommended-low", 80, 50, True),
        ("recommended-quality", 95, 90, True), ("recommended-newer", 90, 90, True),
        ("recommended-null", 100, None, True), ("rejected", 90, 99, True),
    ):
        _store_evaluation(tmp_path, card_id, quality=quality, relevance=relevance, passed=passed)
    # A newer timestamp wins even when its evaluation ID sorts earlier.
    _store_evaluation(tmp_path, "rejected", quality=40, relevance=None, passed=False,
                      evaluation_id="a-rejected-latest", created_at="2026-07-17T10:00:00Z")
    # Equal timestamps use evaluation_id DESC, not insertion order.
    _store_evaluation(tmp_path, "recommended-high", quality=30, relevance=0, passed=False,
                      evaluation_id="a-high-older")
    return client


def _store_evaluation(base_dir, card_id, *, quality, relevance, passed,
                      evaluation_id=None, created_at=NOW):
    evaluation_id = evaluation_id or "z-" + card_id
    with CardRepository(base_dir).connect() as conn:
        CardEvaluationRepository.insert(conn, {
            "evaluation_id": evaluation_id, "card_id": card_id, "card_version": 2,
            "profile_version": 1, "prompt_version": evaluation_id, "model": "external-agent",
            "provider_id": "test", "quality": quality, "relevance": relevance,
            "attribution_kind": "project" if passed else None,
            "attribution_ref": "产品研究" if passed else None, "content_type": "tool",
            "suggested_action": "read", "paused_landmark": False, "summary": "评估摘要",
            "relation_note": "支持关注项目", "passed_quality": passed,
            "receipt_id": None, "created_at": created_at,
        })


def test_card_preview_default_preserves_catalog_and_adds_latest_evaluation(evaluated_preview):
    response = evaluated_preview.get("/api/card-preview")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 7
    assert {item["card_id"] for item in page["items"]} == {
        "recommended-high", "recommended-low", "recommended-quality", "recommended-newer",
        "recommended-null", "rejected", "unevaluated",
    }
    assert all("evaluation" in item for item in page["items"])
    by_id = {item["card_id"]: item["evaluation"] for item in page["items"]}
    assert by_id["unevaluated"] is None
    assert by_id["recommended-high"] == {
        "quality": 90, "relevance": 90, "passed_quality": True, "attribution_kind": "project",
        "attribution_ref": "产品研究", "content_type": "tool", "suggested_action": "read",
        "summary": "评估摘要", "relation_note": "支持关注项目",
        "evaluated_at": "2026-07-16T10:00:00.000000Z",
        "prompt_version": "z-recommended-high", "profile_version": 1, "model": "external-agent",
    }
    assert by_id["rejected"]["passed_quality"] is False
    assert by_id["rejected"]["quality"] == 40
    for card_id, evaluation in by_id.items():
        detail = evaluated_preview.get(f"/api/card-preview/{card_id}").json()
        assert detail["evaluation"] == evaluation
        assert "intelligence_status" not in detail
    assert evaluated_preview.get("/api/card-preview?recommended=").json() == page


def test_card_preview_recommended_uses_latest_and_sorts_in_sql(evaluated_preview):
    response = evaluated_preview.get("/api/card-preview?recommended=1&sort=relevance_desc")
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 5
    assert [item["card_id"] for item in page["items"]] == [
        "recommended-quality", "recommended-newer", "recommended-high", "recommended-low", "recommended-null",
    ]
    assert all(item["evaluation"]["passed_quality"] is True for item in page["items"])
    assert evaluated_preview.get("/api/card-preview?recommended=1&status=processing").json()["total"] == 0
    assert evaluated_preview.get("/api/card-preview?recommended=1&status=processing&sort=relevance_desc").json()["items"] == []


def test_card_preview_recommended_combines_search_platform_completeness_and_pagination(evaluated_preview):
    params = {"recommended": "1", "sort": "relevance_desc", "platform": "x", "query": "关注 产品",
              "completeness": "partial", "limit": 1, "offset": 1}
    page = evaluated_preview.get("/api/card-preview", params=params).json()
    assert page["total"] == 3
    assert page["offset"] == 1
    assert [item["card_id"] for item in page["items"]] == ["recommended-newer"]
    params["offset"] = 10
    page = evaluated_preview.get("/api/card-preview", params=params).json()
    assert page["total"] == 3
    assert page["items"] == []
    params.update(query="无匹配", offset=0)
    assert evaluated_preview.get("/api/card-preview", params=params).json()["total"] == 0


@pytest.mark.parametrize("value", ["0", "true", "2", "-1", "yes"])
def test_card_preview_rejects_invalid_recommended(tmp_path, value):
    response = _client(tmp_path).get("/api/card-preview", params={"recommended": value})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_recommended"


@pytest.mark.parametrize("params", [{"sort": "relevance_desc"}, {"sort": "relevance_desc", "recommended": ""}])
def test_card_preview_relevance_sort_requires_recommended(tmp_path, params):
    response = _client(tmp_path).get("/api/card-preview", params=params)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_sort"


@pytest.mark.parametrize("path", ["/api/card-preview", "/api/card-preview/facets"])
@pytest.mark.parametrize("params", [
    {"published_since": "not-a-date"}, {"published_until": "2026-02-30"},
    {"published_until": "2026-09-16T12:00:00"}, {"published_until": ""},
    {"published_since": "2026-09-17", "published_until": "2026-09-16"},
])
def test_card_preview_invalid_publication_range(tmp_path, path, params):
    response = _client(tmp_path).get(path, params=params)
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_published_range"
    assert set(response.json()) == {"code", "message"}


def test_card_preview_publication_dates_use_shanghai_midnight_and_inclusive_bounds(tmp_path):
    client = _client(tmp_path)
    for card_id, stamp in (("before", "2026-09-15T15:59:59Z"),
                           ("boundary", "2026-09-16T00:00:00+08:00"),
                           ("after", "2026-09-15T16:00:00.000001Z"), ("unknown", None)):
        _store_card(tmp_path, card_id=card_id, platform="rss", title=card_id, author="作者")
        _set_card_publication(tmp_path, card_id, stamp)
    path = "/api/card-preview"
    page = client.get(path, params={"published_until": "2026-09-16", "limit": 1}).json()
    assert page["total"] == 2 and [r["card_id"] for r in page["items"]] == ["boundary"]
    assert client.get(path, params={"published_until": "2026-09-15T16:00:00Z", "limit": 1}).json() == page
    assert client.get(path, params={"published_until": "2026-09-16", "offset": 1}).json()["items"][0]["card_id"] == "before"
    page = client.get(path, params={"published_since": "2026-09-16", "published_until": "2026-09-16"}).json()
    assert [r["card_id"] for r in page["items"]] == ["boundary"]
    assert client.get(path, params={"published_since": "2026-09-16"}).json()["total"] == 2
