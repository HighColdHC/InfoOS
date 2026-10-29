import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.sources.configuration import (
    database_source_to_connector_config,
    normalize_youtube_channel_identifier,
)
from backend.domain.sources.registry import (
    capability_catalog,
    is_research_source_type,
    research_source_type_pairs,
    source_task_view,
)
from backend.infrastructure.storage.repositories import SourceRepository


def test_research_capability_is_registry_owned_and_projects_to_catalog():
    pairs = research_source_type_pairs()
    assert tuple(pair for pair in pairs if pair[1] != "research") == (("zsxq", "keyword"),)
    assert {platform for platform, kind in pairs if kind == "research"} == {
        p["platform"] for p in capability_catalog() if p["research"] is not None
    }
    assert is_research_source_type("zsxq", "keyword") is True
    assert source_task_view("zsxq", "keyword") == "research"
    assert source_task_view("zsxq", "topics") == "collection"
    assert source_task_view("youtube", "keyword") == "collection"
    assert source_task_view("unknown", "keyword") == "collection"
    assert source_task_view("github", "research") == "research"
    assert source_task_view("web", "research") == "collection"

    catalog_pairs = {
        (platform["platform"], source["source_type"])
        for platform in capability_catalog()
        for source in platform["source_types"]
        if source["research_capable"]
    }
    assert catalog_pairs == {("zsxq", "keyword")}
    assert all(
        "research_capable" in source
        for platform in capability_catalog()
        for source in platform["source_types"]
    )


def _seed_rss_source(
    tmp_path: Path,
    *,
    source_id: str = "rss_hn",
    identifier: str = "https://news.ycombinator.com/rss",
    cadence_hours: float = 5.0,
    enabled: bool = True,
    source_lane: str = "core",
    topics: list[str] | None = None,
    purpose: str = "public technology signals",
    review_policy: str = "auto_collect",
) -> None:
    from backend.domain.sources.configuration import validate_source_payload

    payload = validate_source_payload(
        {
            "source_id": source_id,
            "platform": "rss",
            "source_type": "feed",
            "identifier": identifier,
            "display_name": source_id,
            "default_domain_id": None,
            "cadence_hours": cadence_hours,
            "notes": "",
            "enabled": enabled,
            "connector_id": source_id,
        }
    )
    payload["raw_metadata"] = {
        "source_lane": source_lane,
        "topics": topics or ["technology", "startups"],
        "purpose": purpose,
        "review_policy": review_policy,
    }
    SourceRepository(tmp_path).upsert_source(payload)


def test_database_rss_source_projects_to_rss_connector_config(tmp_path):
    _seed_rss_source(tmp_path)

    sources = SourceRepository(tmp_path).list_sources()
    assert len(sources) == 1
    connector = database_source_to_connector_config(sources[0])

    assert connector["connector_id"] == "rss_hn"
    assert connector["type"] == "rss"
    assert connector["feed_url"] == "https://news.ycombinator.com/rss"
    assert connector["enabled"] is True
    assert connector["source_id"] == "rss_hn"
    assert connector["source_lane"] == "core"
    assert connector["topics"] == ["technology", "startups"]
    assert connector["purpose"] == "public technology signals"
    assert connector["review_policy"] == "auto_collect"


def test_database_x_creator_source_projects_to_opencli_connector(tmp_path):
    from backend.domain.sources.configuration import validate_source_payload

    payload = validate_source_payload(
        {
            "source_id": "x_example_studio",
            "platform": "x",
            "source_type": "creator",
            "identifier": "example_studio",
            "display_name": "ExampleStudio",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "",
            "enabled": True,
            "connector_id": "opencli_x_example_studio",
        }
    )
    payload["raw_metadata"] = {
        "source_lane": "core",
        "topics": ["ai"],
        "purpose": "track ExampleStudio posts",
        "review_policy": "auto_collect",
        "max_items_per_run": 15,
    }
    SourceRepository(tmp_path).upsert_source(payload)

    sources = SourceRepository(tmp_path).list_sources()
    connector = database_source_to_connector_config(sources[0])

    assert connector["type"] == "opencli"
    assert connector["command"] == ["twitter", "tweets", "example_studio", "--limit", "15"]
    assert connector["source_platform"] == "x"
    assert connector["source_type"] == "social_post"
    assert connector["metadata_mapping"] == {
        "views": "views",
        "likes": "likes",
        "replies": "replies",
        "retweets": "retweets",
        "bookmarks": "bookmarks",
        "quotes": "quotes",
    }


def test_database_x_creator_defaults_to_larger_validation_batch(tmp_path):
    from backend.domain.sources.configuration import validate_source_payload

    payload = validate_source_payload(
        {
            "source_id": "x_example_studio",
            "platform": "x",
            "source_type": "creator",
            "identifier": "example_studio",
            "display_name": "ExampleStudio",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "",
            "enabled": True,
            "connector_id": "opencli_x_example_studio",
        }
    )
    SourceRepository(tmp_path).upsert_source(payload)

    connector = database_source_to_connector_config(SourceRepository(tmp_path).list_sources()[0])

    assert connector["command"] == ["twitter", "tweets", "example_studio", "--limit", "50"]


def test_database_x_list_source_projects_to_opencli_connector(tmp_path):
    from backend.domain.sources.configuration import validate_source_payload

    payload = validate_source_payload(
        {
            "source_id": "x_ai_candidates_list",
            "platform": "x",
            "source_type": "list",
            "identifier": "9000000000000000001",
            "display_name": "X AI Candidates List",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "",
            "enabled": True,
            "connector_id": "opencli_x_ai_candidates_list",
        }
    )
    payload["raw_metadata"] = {
        "source_lane": "candidate",
        "topics": ["ai"],
        "purpose": "aggregate AI creator candidate list timeline",
        "review_policy": "candidate_only",
        "max_items_per_run": 100,
    }
    SourceRepository(tmp_path).upsert_source(payload)

    connector = database_source_to_connector_config(SourceRepository(tmp_path).list_sources()[0])

    assert connector["type"] == "opencli"
    assert connector["command"] == ["twitter", "list-tweets", "9000000000000000001", "--limit", "100"]
    assert connector["source_platform"] == "x"
    assert connector["source_type"] == "social_post"
    assert connector["field_mapping"]["author_name"] == "author"


def test_database_youtube_source_projects_to_opencli_connector(tmp_path):
    from backend.domain.sources.configuration import validate_source_payload

    payload = validate_source_payload(
        {
            "source_id": "youtube_ai_agents",
            "platform": "youtube",
            "source_type": "keyword",
            "identifier": "AI agents",
            "display_name": "YouTube AI Agents",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "",
            "enabled": False,
            "connector_id": "opencli_youtube_ai_agents",
        }
    )
    payload["raw_metadata"] = {
        "source_lane": "core",
        "topics": ["agents"],
        "purpose": "track AI agent videos",
        "review_policy": "auto_collect",
        "max_items_per_run": 10,
    }
    SourceRepository(tmp_path).upsert_source(payload)

    connector = database_source_to_connector_config(SourceRepository(tmp_path).list_sources()[0])

    assert connector["type"] == "opencli"
    assert connector["enabled"] is False
    assert connector["command"] == ["youtube", "search", "AI agents", "--limit", "10", "--sort", "date"]
    assert connector["source_platform"] == "youtube"
    assert connector["source_type"] == "video"


def test_database_youtube_channel_source_normalizes_bare_handle(tmp_path):
    payload = {
        "source_id": "youtube_example_channel",
        "platform": "youtube",
        "source_type": "channel",
        "identifier": "ExampleChannel",
        "display_name": "Example Channel",
        "default_domain_id": None,
        "cadence_hours": 1,
        "notes": "",
        "enabled": True,
        "connector_id": "opencli_youtube_example_channel",
        "raw_metadata": {"max_items_per_run": 1},
    }
    SourceRepository(tmp_path).upsert_source(payload)

    connector = database_source_to_connector_config(SourceRepository(tmp_path).list_sources()[0])

    assert connector["command"] == ["youtube", "channel", "@ExampleChannel", "--limit", "1"]


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("https://www.youtube.com/@ExampleStudio", "@ExampleStudio"),
        ("youtube.com/@ExampleStudio/videos", "@ExampleStudio"),
        (
            "https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa",
            "UCaaaaaaaaaaaaaaaaaaaaaa",
        ),
    ],
)
def test_youtube_channel_source_normalizes_supported_channel_urls(identifier, expected):
    assert normalize_youtube_channel_identifier(identifier) == expected


def test_database_to_connector_configs_skips_disabled_sources(tmp_path):
    _seed_rss_source(tmp_path, source_id="rss_hn", enabled=True)
    # A disabled source (XHS — unsupported platform; will be disabled by
    # validate_source_payload even if requested enabled).
    from backend.domain.sources.configuration import validate_source_payload

    payload = validate_source_payload(
        {
            "source_id": "xhs_ai",
            "platform": "xiaohongshu",
            "source_type": "discovery_pool",
            "identifier": "AI tools",
            "display_name": "XHS AI",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "",
            "enabled": False,
            "connector_id": "xhs_ai",
        }
    )
    payload["raw_metadata"] = {
        "source_lane": "candidate",
        "topics": ["ai"],
        "purpose": "disabled discovery pool",
        "review_policy": "candidate_only",
    }
    SourceRepository(tmp_path).upsert_source(payload)

    rows = SourceRepository(tmp_path).list_sources()
    assert len(rows) == 2

    # include_disabled=True returns all (the disabled xhs is filtered as
    # unsupported, not skipped — it produces an "unsupported" connector).
    all_connectors = [database_source_to_connector_config(row) for row in rows]
    assert any(c.get("type") == "rss" for c in all_connectors)
    assert any(c.get("type") == "unsupported" for c in all_connectors)

    # include_disabled=False filters by the ``enabled`` column on the row.
    enabled_rows = SourceRepository(tmp_path).list_sources(include_disabled=False)
    enabled_connectors = [database_source_to_connector_config(row) for row in enabled_rows]
    assert len(enabled_connectors) == 1
    assert enabled_connectors[0]["type"] == "rss"


def test_database_unsupported_source_type_returns_unsupported_connector(tmp_path):
    from backend.domain.sources.configuration import validate_source_payload

    # This RSS source type is unsupported and must remain disabled.
    payload = validate_source_payload(
        {
            "source_id": "rss_unsupported",
            "platform": "rss",
            "source_type": "account",
            "identifier": "Unsupported RSS type",
            "display_name": "Unsupported RSS type",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "",
            "enabled": True,
            "connector_id": "rss_unsupported",
        }
    )
    payload["raw_metadata"] = {
        "source_lane": "candidate",
        "topics": ["ai"],
        "purpose": "unsupported source type",
        "review_policy": "candidate_only",
    }
    SourceRepository(tmp_path).upsert_source(payload)

    rows = SourceRepository(tmp_path).list_sources(include_disabled=False)
    # Unsupported source types stay out of execution.
    assert len(rows) == 0

    all_rows = SourceRepository(tmp_path).list_sources()
    assert len(all_rows) == 1
    connector = database_source_to_connector_config(all_rows[0])
    assert connector["type"] == "unsupported"
    assert connector["source_platform"] == "rss"
    assert connector["source_type"] == "account"


def test_database_row_to_connector_config_round_trip(tmp_path):
    _seed_rss_source(tmp_path)
    row = SourceRepository(tmp_path).list_sources()[0]
    connector = database_source_to_connector_config(row)
    assert connector["source_id"] == "rss_hn"
    assert connector["connector_id"] == "rss_hn"
    assert connector["type"] == "rss"
    assert connector["feed_url"] == "https://news.ycombinator.com/rss"
    assert connector["source_lane"] == "core"
    assert connector["topics"] == ["technology", "startups"]


# --- Tests for helpers that remain pure-function (no DB required) ---
