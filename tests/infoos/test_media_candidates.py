from pathlib import Path
from typing import Optional

import pytest

from backend.application.enrichment.media.candidates import (
    card_expects_downloadable_media,
    collect_media_candidates,
)
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository


def _upsert_card(tmp_path: Path, *, platform: str, raw_payload: dict, source_url: Optional[str] = None) -> dict:
    payload = {
        "card_id": f"card_{platform}",
        "source_id": None,
        "source_platform": platform,
        "source_type": "video" if platform in {"youtube", "douyin"} else "social_post",
        "source_url": source_url or raw_payload.get("url") or f"https://example.com/{platform}",
        "source_author": "author",
        "title": f"{platform} title",
        "content": "body",
        "summary": "",
        "primary_domain_id": None,
        "secondary_domain_ids": [],
        "published_at": "2026-07-06T00:00:00Z",
        "captured_at": "2026-07-06T00:00:01Z",
        "processed_at": "2026-07-06T00:00:02Z",
        "raw_payload": raw_payload,
    }
    CardRepository(tmp_path).upsert_card(payload)
    return payload


def test_collects_douyin_play_url_and_cover_url(tmp_path):
    card = _upsert_card(
        tmp_path,
        platform="douyin",
        raw_payload={
            "play_url": "https://v.douyin.example/video.mp4",
            "cover_url": "https://p.douyin.example/cover.jpeg",
        },
    )

    candidates = collect_media_candidates(card, tmp_path)

    assert [(c.kind, c.url) for c in candidates] == [
        ("video", "https://v.douyin.example/video.mp4"),
        ("image", "https://p.douyin.example/cover.jpeg"),
    ]
    assert card_expects_downloadable_media(card) is True


def test_collects_scys_detail_images_and_videos_from_nested_raw_metadata(tmp_path):
    card = _upsert_card(
        tmp_path,
        platform="scys",
        raw_payload={
            "raw_payload": {
                "images": ["https://cdn.example.com/inline-image"],
                "videos": ["https://cdn.example.com/inline-video"],
                "links": ["https://example.feishu.cn/docx/ABC123"],
            }
        },
    )

    candidates = collect_media_candidates(card, tmp_path)

    assert [(candidate.kind, candidate.url) for candidate in candidates] == [
        ("image", "https://cdn.example.com/inline-image"),
        ("video", "https://cdn.example.com/inline-video"),
    ]


def test_collects_block_enclosure_without_extension_when_type_is_media(tmp_path):
    card = _upsert_card(tmp_path, platform="rss", raw_payload={})
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_rss_enclosure",
            "card_id": card["card_id"],
            "position": 1,
            "layer": "source",
            "block_kind": "video",
            "status": "enriched",
            "body": "",
            "source_url": "https://cdn.example.com/video-stream",
            "provenance": "test",
            "raw_payload": {
                "enclosures": [{"url": "https://cdn.example.com/video-stream", "type": "video/mp4"}],
            },
        }
    )

    candidates = collect_media_candidates(card, tmp_path)

    assert len(candidates) == 1
    assert candidates[0].kind == "video"
    assert candidates[0].url == "https://cdn.example.com/video-stream"


def test_youtube_watch_url_expects_media_even_without_direct_candidates(tmp_path):
    card = _upsert_card(
        tmp_path,
        platform="youtube",
        raw_payload={"url": "https://www.youtube.com/watch?v=abc123"},
    )

    assert collect_media_candidates(card, tmp_path) == []
    assert card_expects_downloadable_media(card) is True


def test_reddit_text_post_does_not_require_downloadable_media(tmp_path):
    card = _upsert_card(
        tmp_path,
        platform="reddit",
        source_url="https://www.reddit.com/r/OpenAI/comments/example/text_post/",
        raw_payload={"selftext": "A complete text-only Reddit post."},
    )

    assert collect_media_candidates(card, tmp_path) == []
    assert card_expects_downloadable_media(card) is False


@pytest.mark.parametrize(
    "raw_payload",
    [
        {"opencli_record": {"is_gallery": True, "gallery_urls": []}},
        {"opencli_record": {"post_hint": "image"}},
        {"raw_payload": {"opencli_record": {"video": {"is_video": True}}}},
        {"media": [{"type": "video"}]},
        {
            "opencli_record": {
                "is_self": True,
                "selftext": "A video post can still carry text.",
                "secure_media": {
                    "reddit_video": {"fallback_url": "https://v.redd.it/abc/DASH_720.mp4"}
                },
            }
        },
    ],
)
def test_reddit_explicit_media_facts_require_download_even_without_candidates(
    tmp_path,
    raw_payload,
):
    card = _upsert_card(
        tmp_path,
        platform="reddit",
        source_url="https://www.reddit.com/r/OpenAI/comments/example/media_post/",
        raw_payload=raw_payload,
    )

    assert collect_media_candidates(card, tmp_path) == []
    assert card_expects_downloadable_media(card) is True


@pytest.mark.parametrize(
    "raw_payload",
    [
        {"opencli_record": {"is_self": True, "video_url": "https://v.redd.it/abc"}},
        {
            "opencli_record": {
                "is_self": True,
                "url_overridden_by_dest": "https://i.redd.it/example.jpg",
            }
        },
    ],
)
def test_reddit_explicit_media_urls_override_self_post_fact(tmp_path, raw_payload):
    card = _upsert_card(
        tmp_path,
        platform="reddit",
        source_url="https://www.reddit.com/r/OpenAI/comments/example/media_post/",
        raw_payload=raw_payload,
    )

    assert card_expects_downloadable_media(card) is True


def test_reddit_unknown_post_does_not_silently_opt_out_of_media(tmp_path):
    card = _upsert_card(
        tmp_path,
        platform="reddit",
        source_url="https://www.reddit.com/r/OpenAI/comments/example/unknown_post/",
        raw_payload={"opencli_record": {"title": "No explicit media facts"}},
    )

    assert card_expects_downloadable_media(card) is True


def test_x_candidate_requires_matching_trusted_parent_record(tmp_path):
    media_url = "https://pbs.twimg.com/media/abc.jpg"
    mismatched = _upsert_card(
        tmp_path,
        platform="x",
        source_url="https://x.com/alice/status/123",
        raw_payload={"opencli_record": {"id_str": "999", "media": [{"url": media_url, "type": "photo"}]}},
    )
    assert collect_media_candidates(mismatched, tmp_path) == []

    matched = {**mismatched, "raw_payload": {"opencli_record": {
        "rest_id": "123",
        "media": [{"url": media_url, "type": "photo"}],
    }}}
    assert [(candidate.url, candidate.metadata) for candidate in collect_media_candidates(matched, tmp_path)] == [
        (media_url, {"tweet_id": "123"})
    ]


def test_x_candidate_rejects_arbitrary_block_cdn_injection(tmp_path):
    card = _upsert_card(
        tmp_path,
        platform="x",
        source_url="https://x.com/alice/status/123",
        raw_payload={"opencli_record": {"id": "123", "content": "https://pbs.twimg.com/media/injected.jpg"}},
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_x_injected",
            "card_id": card["card_id"],
            "position": 1,
            "layer": "source",
            "block_kind": "image",
            "status": "enriched",
            "body": "https://pbs.twimg.com/media/injected.jpg",
            "source_url": "https://pbs.twimg.com/media/injected.jpg",
            "provenance": "untrusted",
            "raw_payload": {"media_url": "https://video.twimg.com/injected.mp4"},
        }
    )
    assert collect_media_candidates(card, tmp_path) == []


@pytest.mark.parametrize(
    ("block_kind", "content_role"),
    [
        ("quote", "quoted"),
        ("repost_original", "retweeted"),
        ("reply_parent", "replied_to"),
    ],
)
def test_x_candidate_accepts_one_level_relation_block_media(
    tmp_path,
    block_kind,
    content_role,
):
    card = _upsert_card(
        tmp_path,
        platform="x",
        source_url="https://x.com/alice/status/123",
        raw_payload={"opencli_record": {"id": "123", "text": "primary"}},
    )
    media_url = "https://video.twimg.com/ext_tw_video/456/pu/vid/720x720/relation.mp4"
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": f"block_x_{block_kind}",
            "card_id": card["card_id"],
            "position": 3,
            "layer": "context",
            "block_kind": block_kind,
            "status": "enriched",
            "body": "related post",
            "source_url": "https://x.com/bob/status/456",
            "provenance": "trusted X relation",
            "raw_payload": {
                "content_role": content_role,
                "relation_depth": 1,
                "relation_source_url": "https://x.com/bob/status/456",
                "media_urls": [media_url],
            },
        }
    )

    candidates = collect_media_candidates(card, tmp_path)

    assert [(candidate.kind, candidate.url) for candidate in candidates] == [("video", media_url)]
    assert candidates[0].metadata == {
        "tweet_id": "456",
        "relation_role": content_role,
        "relation_depth": 1,
        "relation_block_kind": block_kind,
        "relation_source_url": "https://x.com/bob/status/456",
    }


def test_x_relation_provenance_wins_when_primary_record_repeats_same_media_url(tmp_path):
    media_url = "https://pbs.twimg.com/media/shared.jpg"
    card = _upsert_card(
        tmp_path,
        platform="x",
        source_url="https://x.com/alice/status/123",
        raw_payload={"opencli_record": {
            "id": "123",
            "media": [{"url": media_url, "type": "photo"}],
        }},
    )
    CardContentBlockRepository(tmp_path).upsert_block({
        "block_id": "block_x_quote_shared",
        "card_id": card["card_id"],
        "position": 3,
        "layer": "context",
        "block_kind": "quote",
        "status": "enriched",
        "body": "quoted",
        "source_url": "https://x.com/bob/status/456",
        "provenance": "relation",
        "raw_payload": {
            "content_role": "quoted",
            "relation_depth": 1,
            "relation_source_url": "https://x.com/bob/status/456",
            "media_urls": [media_url],
        },
    })

    candidates = collect_media_candidates(card, tmp_path)

    assert len(candidates) == 1
    assert candidates[0].metadata["tweet_id"] == "456"
    assert candidates[0].metadata["relation_role"] == "quoted"


@pytest.mark.parametrize(
    ("payload_override", "source_url"),
    [
        ({"relation_depth": 2}, "https://x.com/bob/status/456"),
        ({"content_role": "primary"}, "https://x.com/bob/status/456"),
        ({}, "https://evil.example/bob/status/456"),
        ({"media_urls": ["https://pbs.twimg.com.evil.example/media/a.jpg"]}, "https://x.com/bob/status/456"),
    ],
)
def test_x_candidate_rejects_untrusted_relation_block_media(tmp_path, payload_override, source_url):
    card = _upsert_card(
        tmp_path,
        platform="x",
        source_url="https://x.com/alice/status/123",
        raw_payload={"opencli_record": {"id": "123", "text": "primary"}},
    )
    payload = {
        "content_role": "quoted",
        "relation_depth": 1,
        "relation_source_url": source_url,
        "media_urls": ["https://pbs.twimg.com/media/relation.jpg"],
        **payload_override,
    }
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_x_quote",
            "card_id": card["card_id"],
            "position": 3,
            "layer": "context",
            "block_kind": "quote",
            "status": "enriched",
            "body": "related post",
            "source_url": source_url,
            "provenance": "relation",
            "raw_payload": payload,
        }
    )

    assert collect_media_candidates(card, tmp_path) == []


def test_x_candidate_rejects_unsafe_cdn_url_even_for_matching_record(tmp_path):
    for media_url in (
        "http://pbs.twimg.com/media/a.jpg",
        "https://user@pbs.twimg.com/media/a.jpg",
        "https://pbs.twimg.com:444/media/a.jpg",
        "https://pbs.twimg.com.evil.example/media/a.jpg",
    ):
        card = _upsert_card(
            tmp_path,
            platform="x",
            source_url="https://x.com/alice/status/123",
            raw_payload={"opencli_record": {
                "id": "123",
                "media": [{"url": media_url, "type": "photo"}],
            }},
        )
        assert collect_media_candidates(card, tmp_path) == []


@pytest.mark.parametrize("compact", [False, True])
def test_x_candidate_reads_trusted_record_from_card_builder_model_dump(tmp_path, compact):
    from backend.domain.models.intel_card import IntelCard

    media_url = "https://pbs.twimg.com/media/from-builder.jpg"
    model = IntelCard(
        card_id="card_x_builder",
        source_platform="x",
        source_type="social_post",
        source_url="https://x.com/alice/status/123",
        captured_at="2026-07-14T00:00:00Z",
        processed_at="2026-07-14T00:00:00Z",
        title="post",
        normalized_content="body",
        summary="",
        workflow_status="new",
        trace={"raw_item_id": "raw-x-builder"},
        raw_payload={
            "opencli_record": {
                "rest_id": "123",
                "media": [{"url": media_url, "type": "photo"}],
            }
        },
    )
    dumped = model.model_dump(mode="json")
    card = {
        "card_id": model.card_id,
        "source_platform": model.source_platform,
        "source_type": model.source_type,
        "source_url": model.source_url,
        "title": model.title,
        "content": model.normalized_content,
        "captured_at": model.captured_at,
        "processed_at": model.processed_at,
        "raw_payload": dumped,
    }
    if compact:
        from backend.application.pipeline.information_card_builder import information_card_payload
        from backend.domain.models.raw_item import RawItem
        from backend.infrastructure.storage.repositories import RawItemRepository

        raw = RawItem(raw_item_id="raw-x-builder", connector_id="x", source_platform="x",
                      source_type="social_post", source_url=model.source_url, captured_at=model.captured_at,
                      raw_metadata=model.raw_payload)
        RawItemRepository(tmp_path).upsert_raw_items([raw])
        card = information_card_payload(model.model_copy(update={
            "raw_connector_id": "x", "raw_item_id": raw.raw_item_id,
            "processor_version": "test", "source_run_id": None}))
    CardRepository(tmp_path).upsert_card(card)
    stored = CardRepository(tmp_path).get_card(model.card_id)
    assert stored is not None
    assert [candidate.url for candidate in collect_media_candidates(stored, tmp_path)] == [media_url]

    dumped["raw_payload"]["opencli_record"]["rest_id"] = "999"
    if compact:
        RawItemRepository(tmp_path).upsert_raw_items([raw.model_copy(update={"raw_metadata": dumped["raw_payload"]})])
    else:
        card["raw_payload"] = dumped
    CardRepository(tmp_path).upsert_card(card)
    mismatched = CardRepository(tmp_path).get_card(model.card_id)
    assert mismatched is not None
    assert collect_media_candidates(mismatched, tmp_path) == []
