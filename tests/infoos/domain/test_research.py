from __future__ import annotations

import pytest

from backend.domain.research import (
    ResearchRequestError,
    canonical_research_item_identity,
    normalize_research_request,
)


def test_normalize_research_request_deduplicates_keywords_and_aliases() -> None:
    request = normalize_research_request(
        {
            "name": "  AI 出海  ",
            "keywords": [" AI ", "ai", "Web 出海"],
            "date_range": {
                "start_date": "2025-01-01",
                "end_date": "2026-07-31",
                "timezone": "Asia/Shanghai",
            },
            "platforms": [
                {"platform": "twitter", "max_items": 20, "options": {}},
                {
                    "platform": "rss",
                    "max_items": 20,
                    "options": {"source_ids": ["rss-1", "rss-1"]},
                },
            ],
        }
    )

    assert request["name"] == "AI 出海"
    assert request["keywords"] == ["AI", "Web 出海"]
    assert request["platforms"][0]["platform"] == "x"
    assert request["platforms"][1]["options"]["source_ids"] == ["rss-1"]


def test_normalize_research_request_rejects_secret_options() -> None:
    with pytest.raises(ResearchRequestError) as exc_info:
        normalize_research_request(
            {
                "name": "test",
                "keywords": ["ai"],
                "date_range": {
                    "start_date": "2025-01-01",
                    "end_date": "2025-02-01",
                    "timezone": "UTC",
                },
                "platforms": [
                    {
                        "platform": "github",
                        "max_items": 10,
                        "options": {"token": "secret"},
                    }
                ],
            }
        )

    assert exc_info.value.code == "invalid_request"


def test_research_identity_prefers_provider_id_and_removes_volatile_query() -> None:
    assert canonical_research_item_identity(
        "xiaohongshu",
        "note-42",
        "https://www.xiaohongshu.com/explore/note-42?xsec_token=first",
    ) == canonical_research_item_identity(
        "xiaohongshu",
        "note-42",
        "https://www.xiaohongshu.com/explore/note-42?xsec_token=second",
    )
    assert canonical_research_item_identity(
        "xiaohongshu",
        "https://www.xiaohongshu.com/explore/note-42?xsec_token=first",
        "https://www.xiaohongshu.com/explore/note-42?xsec_token=first",
    ) == canonical_research_item_identity(
        "xiaohongshu",
        "https://www.xiaohongshu.com/explore/note-42?xsec_token=second",
        "https://www.xiaohongshu.com/explore/note-42?xsec_token=second",
    )
    assert "v=abc123" in canonical_research_item_identity(
        "youtube",
        "https://www.youtube.com/watch?v=abc123&utm_source=test",
        "https://www.youtube.com/watch?v=abc123&utm_source=test",
    )


@pytest.mark.parametrize("platform,default_sort,sorts", [
    ("xiaohongshu", "general", ("general", "latest", "most-liked", "most-commented", "most-collected")),
    ("douyin", "general", ("general", "latest", "most-liked")),
    ("zsxq", "latest", ("latest", "earliest")),
    ("github", "latest", ("latest",)),
])
def test_research_platform_sorts_use_registry(platform, default_sort, sorts):
    from backend.api.schemas.research import ResearchPlatformBody

    def request(sort=None):
        body = ResearchPlatformBody(platform=platform, max_items=10, **({"sort": sort} if sort else {}))
        return normalize_research_request({"name": "test", "keywords": ["AI"], "platforms": [body.model_dump()]})

    assert request()["platforms"][0]["sort"] == default_sort
    for sort in sorts:
        assert request(sort)["platforms"][0]["sort"] == sort
    for sort in set(("general", "latest", "earliest", "most-liked", "most-commented", "most-collected", "invalid")) - set(sorts):
        with pytest.raises(ResearchRequestError):
            request(sort)
