from __future__ import annotations

import pytest

from backend.domain.engagement_metrics import (
    X_ENGAGEMENT_METADATA_MAPPING,
    extract_engagement_metrics,
)


@pytest.mark.parametrize(
    ("platform", "payload", "expected"),
    [
        (
            "x",
            {"views": 10, "likes": 9, "replies": 8, "retweets": 7, "bookmarks": 6, "quotes": 5},
            {"view_count": 10, "like_count": 9, "reply_count": 8, "repost_count": 7, "save_count": 6, "quote_count": 5},
        ),
        ("douyin", {"plays": 10, "likes": 9, "comments": 8, "shares": 7}, {"view_count": 10, "like_count": 9, "comment_count": 8, "share_count": 7}),
        ("xiaohongshu", {"likes": 10, "collects": 9, "comments": 8}, {"like_count": 10, "save_count": 9, "comment_count": 8}),
        ("scys", {"like_count": 10, "favorite_count": 9, "comments_count": 8, "coin_count": 7}, {"like_count": 10, "save_count": 9, "comment_count": 8, "coin_count": 7}),
        ("reddit", {"upvotes": 10, "comments": 9}, {"upvote_count": 10, "comment_count": 9}),
        ("hackernews", {"score": 10, "comments": 9}, {"score": 10, "comment_count": 9}),
        ("youtube", {"views": 10, "likes": 9, "comments": 8}, {"view_count": 10, "like_count": 9, "comment_count": 8}),
        ("rss", {"views": 10}, {}),
    ],
)
def test_extract_engagement_metrics_maps_platform_counters(platform, payload, expected) -> None:
    assert extract_engagement_metrics(platform, payload) == expected


def test_extract_engagement_metrics_supports_historical_nested_opencli_payload() -> None:
    payload = {
        "raw_payload": {
            "opencli_record": {
                "views": "2,001",
                "likes": 0,
                "replies": 4,
            }
        }
    }

    assert extract_engagement_metrics("x", payload) == {
        "view_count": 2001,
        "like_count": 0,
        "reply_count": 4,
    }


def test_extract_engagement_metrics_prefers_explicit_metrics_and_ignores_quoted_tweet() -> None:
    payload = {
        "likes": 11,
        "metrics": {"views": 12, "likes": 13},
        "opencli_record": {"views": 99, "likes": 98, "replies": 7},
        "quoted_tweet": {"retweets": 500, "bookmarks": 400},
    }

    assert extract_engagement_metrics("x", payload) == {
        "view_count": 12,
        "like_count": 13,
        "reply_count": 7,
    }


def test_extract_engagement_metrics_omits_unusable_values_without_mutating_payload() -> None:
    payload = {"views": -1, "likes": "unknown", "replies": False, "quotes": 2.5}
    original = dict(payload)

    assert extract_engagement_metrics("x", payload) == {}
    assert payload == original


@pytest.mark.parametrize(
    ("platform", "payload", "expected"),
    [
        ("xiaohongshu", {"likes": "6.9万"}, {"like_count": 69_000}),
        ("xiaohongshu", {"likes": "1.5亿"}, {"like_count": 150_000_000}),
        ("youtube", {"views": "377\u00a0962\u00a0skatījumi"}, {"view_count": 377_962}),
        ("youtube", {"views": "1.234.567 Aufrufe"}, {"view_count": 1_234_567}),
        ("youtube", {"views": "500K views"}, {"view_count": 500_000}),
        ("youtube", {"views": "1.2M views"}, {"view_count": 1_200_000}),
        ("youtube", {"views": "2B"}, {"view_count": 2_000_000_000}),
    ],
)
def test_extract_engagement_metrics_parses_localized_display_counts(
    platform, payload, expected
) -> None:
    assert extract_engagement_metrics(platform, payload) == expected


def test_x_engagement_metadata_mapping_is_read_only() -> None:
    with pytest.raises(TypeError):
        X_ENGAGEMENT_METADATA_MAPPING["views"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize("value", ["1,2", "1,,2", "12,34", "1.23.456"])
def test_extract_engagement_metrics_rejects_malformed_grouping(value: str) -> None:
    assert extract_engagement_metrics("x", {"views": value}) == {}
