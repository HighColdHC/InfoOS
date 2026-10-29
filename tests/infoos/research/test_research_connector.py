from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.domain.models.raw_item import RawItem, RawTimestamps
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.connectors.research import (
    ResearchConnector,
    _opencli_research_config,
)


def _item(item_id: str, *, url: str, published_at: str | None) -> RawItem:
    return RawItem(
        raw_item_id=item_id,
        connector_id="upstream",
        source_platform="x",
        source_type="social_post",
        source_url=url,
        captured_at=published_at or "2026-07-31T00:00:00Z",
        raw_text="AI research",
        raw_timestamps=RawTimestamps(published_at=published_at),
        raw_metadata={"title": "AI research"},
    )


@dataclass
class _FakeOpenCLI:
    items: list[RawItem]

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        return FetchResult(items=self.items, next_cursor=None)


@pytest.mark.parametrize(
    ("platform", "command_prefix"),
    [
        ("x", ["twitter", "search", "agent"]),
        ("youtube", ["youtube", "search", "agent"]),
        ("reddit", ["reddit", "search", "agent"]),
        ("xiaohongshu", ["xiaohongshu", "search", "agent"]),
        ("douyin", ["douyin", "search", "agent"]),
        ("zsxq", ["zsxq", "search", "agent"]),
    ],
)
def test_browser_platform_research_commands_match_registered_opencli_adapters(
    platform,
    command_prefix,
) -> None:
    config = _opencli_research_config(
        platform=platform,
        keyword="agent",
        limit=20,
        connector_id=f"research:batch:{platform}",
    )

    assert config["command"][:3] == command_prefix
    assert config["source_platform"] == platform
    assert config["use_browser_window"] is True


def test_youtube_research_preserves_the_provider_title_for_card_building() -> None:
    config = _opencli_research_config(
        platform="youtube",
        keyword="agent",
        limit=3,
        connector_id="research:batch:youtube",
    )

    assert config["field_mapping"]["title"] == "title"
    assert config["metadata_mapping"]["title"] == "title"


def test_opencli_research_merges_keyword_matches_filters_dates_and_keeps_unknown(
    monkeypatch,
) -> None:
    connector = ResearchConnector(
        connector_id="research:x",
        platform="x",
        source_id="source-1",
        research_batch_id="research-1",
        keywords=["AI", "agents"],
        date_range={
            "start_date": "2026-01-01",
            "end_date": "2026-12-31",
            "timezone": "UTC",
        },
        max_items=10,
    )
    responses = iter(
        [
            [
                _item("same", url="https://x.com/1", published_at="2026-03-01T00:00:00Z"),
                _item("old", url="https://x.com/old", published_at="2025-03-01T00:00:00Z"),
            ],
            [
                _item("same", url="https://x.com/1", published_at="2026-03-01T00:00:00Z"),
                _item("unknown", url="https://x.com/2", published_at=None),
            ],
        ]
    )
    monkeypatch.setattr(
        connector,
        "_opencli_connector",
        lambda _config: _FakeOpenCLI(next(responses)),
    )

    result = connector.fetch_new_items(None)

    assert [item.raw_item_id for item in result.items] == ["same", "unknown"]
    assert result.items[0].raw_metadata["matched_keywords"] == ["AI", "agents"]
    assert result.items[0].raw_metadata["source_scope"] == "research"
    assert result.items[0].raw_metadata["research_batch_id"] == "research-1"
    assert connector.coverage_evidence["duplicate_count"] == 1
    assert connector.coverage_evidence["date_filtered_count"] == 1
    assert connector.coverage_evidence["unknown_date_count"] == 1


def test_research_dedup_ignores_volatile_query_for_url_shaped_item_ids() -> None:
    connector = ResearchConnector(
        connector_id="research:batch:xiaohongshu",
        platform="xiaohongshu",
        source_id="source-1",
        research_batch_id="research-1",
        keywords=["AI"],
        date_range=None,
        max_items=10,
    )
    first_url = "https://www.xiaohongshu.com/explore/note-42?xsec_token=first"
    second_url = "https://www.xiaohongshu.com/explore/note-42?xsec_token=second"
    first = _item(first_url, url=first_url, published_at=None).model_copy(
        update={"source_platform": "xiaohongshu"}
    )
    second = _item(second_url, url=second_url, published_at=None).model_copy(
        update={"source_platform": "xiaohongshu"}
    )

    items, counts = connector._merge_filter([(first, "AI"), (second, "AI")])

    assert len(items) == 1
    assert counts["duplicate_count"] == 1


def test_github_and_hackernews_use_distinct_public_provider_paths(monkeypatch) -> None:
    github = ResearchConnector(
        connector_id="research:github",
        platform="github",
        source_id="github-source",
        research_batch_id="research-1",
        keywords=["agent"],
        date_range=None,
        max_items=5,
    )
    seen: list[str] = []

    def fake_github(url, *, params):
        seen.append(url)
        return {
            "items": [
                {
                    "id": 1,
                    "full_name": "openai/example",
                    "html_url": "https://github.com/openai/example",
                    "description": "agent",
                    "updated_at": "2026-07-01T00:00:00Z",
                    "owner": {"login": "openai"},
                }
            ]
        }

    monkeypatch.setattr(github, "_get_json", fake_github)
    assert github.fetch_new_items(None).items[0].source_type == "repository"
    assert seen == ["https://api.github.com/search/repositories"]

    hackernews = ResearchConnector(
        connector_id="research:hackernews",
        platform="hackernews",
        source_id="hn-source",
        research_batch_id="research-1",
        keywords=["agent"],
        date_range=None,
        max_items=5,
    )

    def fake_hn(url, *, params):
        seen.append(url)
        return {
            "hits": [
                {
                    "objectID": "42",
                    "title": "Agent story",
                    "created_at": "2026-07-01T00:00:00Z",
                    "author": "pg",
                }
            ]
        }

    monkeypatch.setattr(hackernews, "_get_json", fake_hn)
    assert hackernews.fetch_new_items(None).items[0].source_type == "story"
    assert seen[-1] == "https://hn.algolia.com/api/v1/search_by_date"


def test_producthunt_path_is_explicitly_incomplete(monkeypatch) -> None:
    connector = ResearchConnector(
        connector_id="research:producthunt",
        platform="producthunt",
        source_id="ph-source",
        research_batch_id="research-1",
        keywords=["AI"],
        date_range=None,
        max_items=5,
    )
    monkeypatch.setattr(
        connector,
        "_opencli_connector",
        lambda _config: _FakeOpenCLI(
            [_item("product", url="https://producthunt.com/posts/example", published_at=None)]
        ),
    )

    result = connector.fetch_new_items(None)

    assert len(result.items) == 1
    assert connector.coverage_evidence["completeness"] == "incomplete"
    assert "visible Product Hunt feed" in connector.coverage_evidence["limitation"]


@pytest.mark.parametrize("platform,sort", [("xiaohongshu", "general"), ("xiaohongshu", "latest"), ("xiaohongshu", "most-liked"), ("xiaohongshu", "most-commented"), ("xiaohongshu", "most-collected"), ("douyin", "general"), ("douyin", "latest"), ("douyin", "most-liked")])
def test_keyword_research_passes_nondefault_sort_to_opencli(platform, sort):
    config = _opencli_research_config(platform=platform, keyword="AI", limit=20, connector_id="research", sort=sort)
    expected = [platform, "search", "AI", "--limit", "20"]
    if sort != "general":
        expected.extend(["--sort", sort])
    assert config["command"] == expected
    assert config["result_order"] == ("newest_first" if sort == "latest" else "snapshot")


@pytest.mark.parametrize("platform", ["xiaohongshu", "douyin"])
def test_omitted_research_sort_survives_projection_and_connector_building(platform, tmp_path):
    from backend.application.workflows.fetch_support import build_connector
    from backend.domain.sources.configuration_projection import database_source_to_connector_config

    source = {"source_id": "test", "platform": platform, "source_type": "research", "source_scope": "research",
              "raw_metadata": {"research": {"batch_id": "batch", "keywords": ["AI"], "platform_request": {"max_items": 10}}}}
    config = database_source_to_connector_config(source)
    assert config["sort"] == "general"
    assert build_connector(tmp_path, config).sort == "general"
    config.pop("sort")
    assert build_connector(tmp_path, config).sort == "general"
    command = _opencli_research_config(platform=platform, keyword="AI", limit=10, connector_id="test")["command"]
    assert "--sort" not in command
