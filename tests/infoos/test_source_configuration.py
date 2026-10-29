import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.application.usecases.source_config import load_source_configs
from backend.application.usecases.source_activation import set_source_enabled
from backend.application.usecases.source_configuration_lifecycle import (
    DuplicateSourceError,
    ImmutableSourceIdentityError,
    SourceNotFoundError,
    create_source_configuration,
    update_source_configuration,
)
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.domain.sources.configuration import (
    PLATFORM_CADENCE_OPTIONS,
    SUPPORTED_EXECUTION,
    database_source_to_connector_config,
    validate_source_payload,
)
from backend.infrastructure.storage.repositories import SourceRepository


def test_unsupported_source_cannot_enter_fetch_queue():
    from backend.application.usecases.source_capability import source_can_enter_fetch_queue

    source = {
        "source_id": "xhs_creator",
        "platform": "x",
        "source_type": "keyword",
        "identifier": "creator",
        "enabled": True,
        "connector_status": "supported",
    }


    assert source_can_enter_fetch_queue(source) is False


def test_explicitly_validated_source_status_can_enter_fetch_queue():
    from backend.application.usecases.source_capability import source_can_enter_fetch_queue

    source = {
        "source_id": "rss_validated",
        "platform": "rss",
        "source_type": "feed",
        "enabled": True,
        "connector_status": "supported",
    }

    assert source_can_enter_fetch_queue(source) is True


def test_persisted_status_cannot_promote_unimplemented_source_kind():
    from backend.application.usecases.source_capability import source_can_enter_fetch_queue

    source = {
        "source_id": "x_keyword",
        "platform": "x",
        "source_type": "keyword",
        "enabled": True,
        "connector_status": "supported",
    }

    assert source_can_enter_fetch_queue(source) is False


def test_builtin_matrix_covers_every_configurable_platform_source_type():
    from backend.application.usecases.source_capability import resolve_source_capability

    statuses = {
        resolve_source_capability(
            {
                "source_id": f"{platform}_{source_type}",
                "platform": platform,
                "source_type": source_type,
                "identifier": "test",
                "enabled": True,
            }
        ).status
        for platform, source_type in SUPPORTED_EXECUTION
    }

    assert statuses
    assert statuses <= {
        "supported",
        "implemented_uncalibrated",
        "partial",
        "planned",
        "unsupported",
    }
    assert "supported" not in statuses


def test_platform_cadence_rules_block_high_risk_frequency():
    assert 0.25 in PLATFORM_CADENCE_OPTIONS["x"]
    assert 0.25 not in PLATFORM_CADENCE_OPTIONS["youtube"]
    assert 0.25 not in PLATFORM_CADENCE_OPTIONS["reddit"]
    assert 0.5 in PLATFORM_CADENCE_OPTIONS["hackernews"]
    assert 0.25 not in PLATFORM_CADENCE_OPTIONS["douyin"]


def test_validate_source_payload_accepts_every_6_hours_for_x():
    payload = validate_source_payload(
        {
            "platform": "x",
            "source_type": "creator",
            "identifier": "example",
            "display_name": "Example",
            "default_domain_id": None,
            "cadence_hours": 6.0,
            "notes": "",
            "enabled": True,
        }
    )
    assert payload["cadence_hours"] == 6.0
    assert payload["connector_status"] == "implemented_uncalibrated"
    assert payload["enabled"] is True


@pytest.mark.parametrize(
    ("platform", "source_type", "metadata"),
    [
        (
            "rss",
            "feed",
            {"feed_provider": "generic", "feed_content_mode": "feed_only"},
        ),
        (
            "youtube",
            "channel",
            {"feed_provider": "generic", "feed_content_mode": "feed_only"},
        ),
    ],
)
def test_validate_source_payload_rejects_invalid_feed_policy(
    platform: str,
    source_type: str,
    metadata: dict[str, str],
) -> None:
    with pytest.raises(ValueError):
        validate_source_payload(
            {
                "platform": platform,
                "source_type": source_type,
                "identifier": "example",
                "display_name": "Invalid policy",
                "cadence_hours": 24 if platform in {"rss", "youtube"} else 6,
                "enabled": True,
                "raw_metadata": metadata,
            }
        )


def test_validate_source_payload_disables_unsupported_source_type():
    payload = validate_source_payload(
        {
            "platform": "rss",
            "source_type": "account",
            "identifier": "Unsupported RSS type",
            "display_name": "Unsupported RSS type",
            "default_domain_id": None,
            "cadence_hours": 24,
            "notes": "wait for connector",
            "enabled": True,
        }
    )

    assert payload["connector_status"] == "unsupported"
    assert payload["enabled"] is False


def test_load_source_configs_projects_database_row_to_connector_config(tmp_path):
    """Seed an X list source via SourceRepository, then read it back
    through the CLI helper that uses ``database_source_to_connector_config``."""
    payload = validate_source_payload(
        {
            "source_id": "x_ai_list",
            "platform": "x",
            "source_type": "list",
            "identifier": "12345",
            "display_name": "X AI List",
            "default_domain_id": None,
            "cadence_hours": 1,
            "notes": "AI list",
            "enabled": True,
            "connector_id": "x_ai_list",
        }
    )
    # Attach metadata that database_source_to_connector_config pulls from raw_metadata
    payload["raw_metadata"] = {
        "source_lane": "core",
        "topics": ["ai"],
        "purpose": "AI list",
        "review_policy": "auto_collect",
    }
    SourceRepository(tmp_path).upsert_source(payload)

    connectors = load_source_configs(tmp_path)

    assert len(connectors) == 1
    connector = connectors[0]
    assert connector["source_id"] == "x_ai_list"
    assert connector["connector_id"] == "x_ai_list"
    assert connector["source_platform"] == "x"
    assert connector["source_type"] == "social_post"
    assert connector["type"] == "opencli"
    assert connector["command"] == ["twitter", "list-tweets", "12345", "--limit", "50"]
    assert connector["source_lane"] == "core"
    assert connector["topics"] == ["ai"]
    assert connector["purpose"] == "AI list"
    assert connector["review_policy"] == "auto_collect"
    assert connector["field_mapping"]["author_name"] == "author"
    assert connector["metadata_mapping"] == {
        "views": "views",
        "likes": "likes",
        "replies": "replies",
        "retweets": "retweets",
        "bookmarks": "bookmarks",
        "quotes": "quotes",
    }


def test_rss_source_projects_max_items_per_run(tmp_path):
    payload = validate_source_payload(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "AI Feed",
            "default_domain_id": None,
            "cadence_hours": 1,
            "notes": "",
            "enabled": True,
            "connector_id": "rss_ai",
            "raw_metadata": {"max_items_per_run": 7},
        }
    )
    SourceRepository(tmp_path).upsert_source(payload)

    connector = load_source_configs(tmp_path)[0]

    assert connector["type"] == "rss"
    assert connector["max_items_per_run"] == 7


def test_validate_source_payload_supports_next_platform_sources():
    cases = [
        ("reddit", "subreddit", "LocalLLaMA"),
        ("reddit", "keyword", "AI agents"),
        ("hackernews", "feed", "top"),
        ("hackernews", "keyword", "AI agents"),
        ("producthunt", "feed", "today"),
        ("producthunt", "category", "ai-agents"),
        ("github", "releases", "openai/openai-python"),
        ("github", "commits", "openai/openai-python"),
        ("xiaohongshu", "keyword", "AI agents"),
        ("xiaohongshu", "creator", "https://www.xiaohongshu.com/user/profile/000000000000000000000001"),
        ("douyin", "keyword", "AI agents"),
        ("douyin", "creator", "MS4wLjABAAAAExampleSecUid"),
        ("zsxq", "topics", "ai"),
        ("zsxq", "opportunity", "ai"),
        ("zsxq", "keyword", "Obsidian"),
    ]

    for platform, source_type, identifier in cases:
        payload = validate_source_payload(
            {
                "platform": platform,
                "source_type": source_type,
                "identifier": identifier,
                "display_name": identifier,
                "default_domain_id": None,
                "cadence_hours": 5,
                "notes": "",
                "enabled": True,
            }
        )

        assert payload["connector_status"] == "implemented_uncalibrated"
        assert payload["enabled"] is True


def test_validate_source_payload_rejects_empty_zsxq_keyword():
    import pytest

    with pytest.raises(ValueError, match="zsxq keyword requires identifier"):
        validate_source_payload(
            {
                "source_id": "zsxq_empty_keyword",
                "platform": "zsxq",
                "source_type": "keyword",
                "identifier": "  ",
                "display_name": "ZSXQ Empty Keyword",
                "cadence_hours": 5,
                "enabled": True,
            }
        )


def test_legacy_scys_cannot_be_configured_scheduled_or_projected_to_opencli():
    assert not any(platform == "scys" for platform, _source_type in SUPPORTED_EXECUTION)
    with pytest.raises(ValueError, match="unsupported platform: scys"):
        validate_source_payload({
            "platform": "scys", "source_type": "keyword", "identifier": "AI",
            "display_name": "旧来源", "cadence_hours": 5, "enabled": True,
        })
    legacy_connector = database_source_to_connector_config({
        "source_id": "legacy-scys", "platform": "scys", "source_type": "keyword",
        "identifier": "AI", "cadence_hours": 5, "enabled": True,
        "connector_id": "legacy-scys", "raw_metadata": {},
    })
    assert legacy_connector["type"] == "unsupported"
    assert "command" not in legacy_connector


def test_zsxq_keyword_projects_native_website_search_filters():
    source = validate_source_payload(
        {
            "source_id": "zsxq_ai_export",
            "connector_id": "zsxq_ai_export",
            "platform": "zsxq",
            "source_type": "keyword",
            "identifier": "AI 网站出海",
            "display_name": "AI 网站出海",
            "cadence_hours": 5,
            "max_items_per_run": 100,
            "enabled": True,
            "raw_metadata": {
                "zsxq_search": {
                    "sort": "newest",
                    "content_type": "essence",
                    "keywords": ["web出海", "AI网站出海", "网站出海"],
                    "start_date": "2024-07-30",
                    "end_date": "2026-07-30",
                }
            },
        }
    )

    connector = database_source_to_connector_config(source)

    assert connector["command"] == [
        "zsxq",
        "search",
        "AI 网站出海",
        "--group_id",
        "1824528822",
        "--limit",
        "100",
        "--sort",
        "newest",
        "--content-type",
        "essence",
        "--keywords-json",
        '["web出海","AI网站出海","网站出海"]',
        "--start-date",
        "2024-07-30",
        "--end-date",
        "2026-07-30",
    ]
    from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout
    assert connector["timeout_seconds"] == zsxq_command_timeout(connector["command"])


def test_validate_zsxq_keyword_rejects_inverted_date_range():
    with pytest.raises(ValueError, match="start_date must not be later"):
        validate_source_payload(
            {
                "platform": "zsxq",
                "source_type": "keyword",
                "identifier": "AI 网站出海",
                "display_name": "AI 网站出海",
                "cadence_hours": 5,
                "max_items_per_run": 20,
                "enabled": True,
                "raw_metadata": {
                    "zsxq_search": {
                        "start_date": "2026-07-30",
                        "end_date": "2024-07-30",
                    }
                },
            }
        )


@pytest.mark.parametrize("source_type", ["topics", "opportunity"])
def test_validate_source_payload_rejects_unknown_zsxq_tab(source_type: str):
    with pytest.raises(ValueError, match=f"zsxq {source_type} identifier must be one of"):
        validate_source_payload(
            {
                "platform": "zsxq",
                "source_type": source_type,
                "identifier": "invalid-tab",
                "display_name": "Invalid ZSXQ Tab",
                "cadence_hours": 5,
                "enabled": True,
            }
        )


def test_validate_source_payload_rejects_producthunt_feed_alias():
    with pytest.raises(ValueError, match="producthunt feed identifier must be one of"):
        validate_source_payload(
            {
                "platform": "producthunt",
                "source_type": "feed",
                "identifier": "daily-alias",
                "display_name": "Duplicate Product Hunt Alias",
                "cadence_hours": 5,
                "enabled": True,
            }
        )


def test_database_zsxq_sources_project_to_opencli_connectors(tmp_path):
    repo = SourceRepository(tmp_path)
    for source_type, identifier, connector_id in [
        ("topics", "all", "opencli_zsxq_all_topics"),
        ("opportunity", "ai", "opencli_zsxq_ai_opportunity"),
        ("keyword", "Obsidian", "opencli_zsxq_obsidian"),
    ]:
        repo.upsert_source(
            validate_source_payload(
                {
                    "source_id": connector_id,
                    "platform": "zsxq",
                    "source_type": source_type,
                    "identifier": identifier,
                    "display_name": connector_id,
                    "default_domain_id": None,
                    "cadence_hours": 5,
                    "notes": "",
                    "enabled": True,
                    "connector_id": connector_id,
                    "raw_metadata": {"max_items_per_run": 12},
                }
            )
        )

    connectors = {row["source_id"]: database_source_to_connector_config(row) for row in repo.list_sources()}

    assert connectors["opencli_zsxq_all_topics"]["type"] == "opencli"
    assert connectors["opencli_zsxq_all_topics"]["command"] == [
        "zsxq",
        "topics",
        "--group_id",
        "1824528822",
        "--filter",
        "all",
        "--limit",
        "12",
    ]
    assert connectors["opencli_zsxq_all_topics"]["source_platform"] == "zsxq"
    assert connectors["opencli_zsxq_all_topics"]["source_type"] == "article"
    assert connectors["opencli_zsxq_all_topics"]["field_mapping"]["text"] == "full_text"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["title"] == "title"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["topic_id"] == "topic_id"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["tags"] == "tags"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["links"] == "links"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["images"] == "images"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["videos"] == "videos"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["content_source"] == "content_source"
    assert connectors["opencli_zsxq_all_topics"]["metadata_mapping"]["content_source_url"] == "content_source_url"
    assert connectors["opencli_zsxq_all_topics"]["requires_browser_session"] is True
    assert "keep_alive" not in connectors["opencli_zsxq_all_topics"]

    assert connectors["opencli_zsxq_ai_opportunity"]["command"] == [
        "zsxq",
        "opportunity",
        "--group_id",
        "1824528822",
        "--limit",
        "12",
        "--keyword",
        "AI",
    ]
    assert connectors["opencli_zsxq_obsidian"]["command"] == [
        "zsxq",
        "search",
        "Obsidian",
        "--group_id",
        "1824528822",
        "--limit",
        "12",
        "--sort",
        "comprehensive",
        "--content-type",
        "all",
    ]


def test_database_chinese_social_sources_project_to_opencli_connectors(tmp_path):
    repo = SourceRepository(tmp_path)
    for platform, source_type, identifier, connector_id in [
        ("xiaohongshu", "keyword", "AI agents", "opencli_xhs_ai"),
        (
            "xiaohongshu",
            "creator",
            "https://www.xiaohongshu.com/user/profile/000000000000000000000001",
            "opencli_xhs_creator",
        ),
        ("douyin", "keyword", "AI agents", "opencli_douyin_ai"),
        ("douyin", "creator", "MS4wLjABAAAAExampleSecUid", "opencli_douyin_creator"),
    ]:
        repo.upsert_source(
            validate_source_payload(
                {
                    "source_id": connector_id,
                    "platform": platform,
                    "source_type": source_type,
                    "identifier": identifier,
                    "display_name": connector_id,
                    "default_domain_id": None,
                    "cadence_hours": 5,
                    "notes": "",
                    "enabled": True,
                    "connector_id": connector_id,
                    "raw_metadata": {"max_items_per_run": 9},
                }
            )
        )

    connectors = {row["source_id"]: database_source_to_connector_config(row) for row in repo.list_sources()}

    assert connectors["opencli_xhs_ai"]["command"] == ["xiaohongshu", "search", "AI agents", "--limit", "9"]
    assert connectors["opencli_xhs_ai"]["source_platform"] == "xiaohongshu"
    assert connectors["opencli_xhs_ai"]["source_type"] == "social_post"
    assert connectors["opencli_xhs_ai"]["field_mapping"]["text"] == "content"
    assert connectors["opencli_xhs_ai"]["detail_command_template"] == ["xiaohongshu", "note", "{url}"]
    assert connectors["opencli_xhs_ai"]["detail_max_items"] == 9
    assert connectors["opencli_xhs_ai"]["metadata_mapping"]["likes"] == "likes"
    assert connectors["opencli_xhs_ai"]["metadata_mapping"]["tags"] == "tags"
    assert connectors["opencli_xhs_ai"]["metadata_mapping"]["note_url"] == "url"
    assert connectors["opencli_xhs_ai"]["requires_browser_session"] is True
    assert "keep_alive" not in connectors["opencli_xhs_ai"]

    assert connectors["opencli_xhs_creator"]["command"] == [
        "xiaohongshu",
        "user",
        "https://www.xiaohongshu.com/user/profile/000000000000000000000001",
        "--limit",
        "9",
    ]
    assert connectors["opencli_douyin_ai"]["command"] == ["douyin", "search", "AI agents", "--limit", "9"]
    assert connectors["opencli_douyin_ai"]["field_mapping"]["title"] == "desc"
    assert connectors["opencli_douyin_ai"]["metadata_mapping"]["likes"] == "likes"
    assert connectors["opencli_douyin_creator"]["command"] == [
        "douyin",
        "user-videos",
        "MS4wLjABAAAAExampleSecUid",
        "--limit",
        "9",
        "--max_pages",
        "25",
        "--with_comments",
        "false",
        "--comment_limit",
        "50",
    ]
    assert connectors["opencli_douyin_creator"]["type"] == "douyin_creator"
    assert connectors["opencli_douyin_creator"]["timeout_seconds"] == 900
    assert connectors["opencli_douyin_creator"]["field_mapping"]["id"] == "url"
    assert connectors["opencli_douyin_creator"]["metadata_mapping"]["likes"] == "digg_count"


def test_validate_source_payload_rejects_empty_chinese_social_identifiers():
    import pytest

    for platform, source_type in [
        ("xiaohongshu", "keyword"),
        ("xiaohongshu", "creator"),
        ("douyin", "keyword"),
        ("douyin", "creator"),
    ]:
        with pytest.raises(ValueError, match=f"{platform} {source_type} requires identifier"):
            validate_source_payload(
                {
                    "source_id": f"{platform}_{source_type}_empty",
                    "platform": platform,
                    "source_type": source_type,
                    "identifier": "  ",
                    "display_name": "Empty",
                    "cadence_hours": 5,
                    "enabled": True,
                }
            )


def test_douyin_item_projects_to_single_item_provider():
    source = validate_source_payload(
        {
            "source_id": "douyin_item_live",
            "connector_id": "douyin_item_live",
            "platform": "douyin",
            "source_type": "item",
            "identifier": "https://v.douyin.com/qpMfgM5vbuY/",
            "display_name": "单个作品",
            "cadence_hours": 24,
            "enabled": True,
            "raw_metadata": {"max_items_per_run": 99},
        }
    )

    assert source["raw_metadata"]["max_items_per_run"] == 1
    connector = database_source_to_connector_config(source)
    assert connector["type"] == "douyin_item"
    assert connector["max_items_per_run"] == 1
    assert connector["identifier"] == "https://v.douyin.com/qpMfgM5vbuY/"


def test_douyin_item_accepts_bare_short_url_and_rejects_invalid_input():
    accepted = validate_source_payload(
        {
            "platform": "douyin",
            "source_type": "item",
            "identifier": "复制打开抖音 v.iesdouyin.com/example/",
            "display_name": "裸短链",
            "cadence_hours": 24,
            "enabled": True,
        }
    )

    assert accepted["max_items_per_run"] == 1
    with pytest.raises(ValueError, match="exactly one URL"):
        validate_source_payload(
            {
                "platform": "douyin",
                "source_type": "item",
                "identifier": "not a douyin URL",
                "display_name": "Invalid",
                "cadence_hours": 24,
                "enabled": True,
            }
        )


def test_database_reddit_sources_project_to_opencli_connectors(tmp_path):
    repo = SourceRepository(tmp_path)
    subreddit = validate_source_payload(
        {
            "source_id": "reddit_localllama",
            "platform": "reddit",
            "source_type": "subreddit",
            "identifier": "LocalLLaMA",
            "display_name": "r/LocalLLaMA",
            "default_domain_id": None,
            "cadence_hours": 5,
            "notes": "",
            "enabled": True,
            "connector_id": "opencli_reddit_localllama",
            "raw_metadata": {"max_items_per_run": 30},
        }
    )
    keyword = validate_source_payload(
        {
            "source_id": "reddit_ai_agents",
            "platform": "reddit",
            "source_type": "keyword",
            "identifier": "AI agents",
            "display_name": "Reddit AI Agents",
            "default_domain_id": None,
            "cadence_hours": 5,
            "notes": "",
            "enabled": True,
            "connector_id": "opencli_reddit_ai_agents",
            "raw_metadata": {"max_items_per_run": 25},
        }
    )
    repo.upsert_source(subreddit)
    repo.upsert_source(keyword)

    connectors = {row["source_id"]: database_source_to_connector_config(row) for row in repo.list_sources()}

    assert connectors["reddit_localllama"]["type"] == "opencli"
    assert connectors["reddit_localllama"]["command"] == [
        "reddit",
        "subreddit",
        "LocalLLaMA",
        "--sort",
        "new",
        "--limit",
        "30",
    ]
    assert connectors["reddit_localllama"]["source_platform"] == "reddit"
    assert connectors["reddit_localllama"]["source_type"] == "social_post"
    assert connectors["reddit_localllama"]["max_items_per_run"] == 30
    assert "keep_alive" not in connectors["reddit_localllama"]
    assert connectors["reddit_localllama"]["requires_browser_session"] is True
    assert connectors["reddit_localllama"]["metadata_mapping"]["title"] == "title"
    assert connectors["reddit_ai_agents"]["command"] == [
        "reddit",
        "search",
        "AI agents",
        "--sort",
        "new",
        "--time",
        "week",
        "--limit",
        "25",
    ]
    assert "keep_alive" not in connectors["reddit_ai_agents"]


def test_database_hackernews_producthunt_and_github_sources_project_to_connectors(tmp_path):
    repo = SourceRepository(tmp_path)
    for payload in [
        {
            "source_id": "hn_top",
            "platform": "hackernews",
            "source_type": "feed",
            "identifier": "top",
            "display_name": "HN Top",
            "connector_id": "opencli_hn_top",
        },
        {
            "source_id": "ph_ai_agents",
            "platform": "producthunt",
            "source_type": "category",
            "identifier": "ai-agents",
            "display_name": "Product Hunt AI Agents",
            "connector_id": "opencli_ph_ai_agents",
        },
        {
            "source_id": "github_openai_releases",
            "platform": "github",
            "source_type": "releases",
            "identifier": "openai/openai-python",
            "display_name": "openai-python releases",
            "connector_id": "github_openai_releases",
        },
    ]:
        repo.upsert_source(
            validate_source_payload(
                {
                    **payload,
                    "default_domain_id": None,
                    "cadence_hours": 5,
                    "notes": "",
                    "enabled": True,
                    "raw_metadata": {"max_items_per_run": 20},
                }
            )
        )

    connectors = {row["source_id"]: database_source_to_connector_config(row) for row in repo.list_sources()}

    assert connectors["hn_top"]["type"] == "opencli"
    assert connectors["hn_top"]["command"] == ["hackernews", "top", "--limit", "20"]
    assert connectors["hn_top"]["use_browser_window"] is False
    assert connectors["hn_top"]["source_platform"] == "hackernews"
    assert connectors["hn_top"]["metadata_mapping"] == {"title": "title"}
    assert connectors["ph_ai_agents"]["command"] == [
        "producthunt",
        "posts",
        "--category",
        "ai-agents",
        "--limit",
        "20",
    ]
    assert connectors["ph_ai_agents"]["source_platform"] == "producthunt"
    assert connectors["ph_ai_agents"]["use_browser_window"] is False
    assert connectors["ph_ai_agents"]["metadata_mapping"] == {"title": "name"}
    assert connectors["github_openai_releases"]["type"] == "rss"
    assert connectors["github_openai_releases"]["feed_url"] == "https://github.com/openai/openai-python/releases.atom"
    assert connectors["github_openai_releases"]["source_platform"] == "github"
    assert connectors["github_openai_releases"]["source_type"] == "release"
    assert connectors["github_openai_releases"]["max_items_per_run"] == 20


def test_github_sources_have_default_max_items_per_run(tmp_path):
    payload = validate_source_payload(
        {
            "source_id": "github_openai_commits",
            "platform": "github",
            "source_type": "commits",
            "identifier": "openai/openai-python",
            "display_name": "openai-python commits",
            "default_domain_id": None,
            "cadence_hours": 5,
            "notes": "",
            "enabled": True,
            "connector_id": "github_openai_commits",
        }
    )
    SourceRepository(tmp_path).upsert_source(payload)

    connector = database_source_to_connector_config(
        SourceRepository(tmp_path).get_source("github_openai_commits")
    )

    assert connector["type"] == "rss"
    assert connector["max_items_per_run"] == 20


def test_source_repository_updates_and_deletes_source(tmp_path):
    repo = SourceRepository(tmp_path)
    payload = validate_source_payload(
        {
            "source_id": "rss_ai",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "AI Feed",
            "default_domain_id": None,
            "cadence_hours": 1,
            "notes": "original",
            "enabled": True,
            "connector_id": "rss_ai",
        }
    )
    repo.upsert_source(payload)

    updated = validate_source_payload({**payload, "display_name": "AI Feed Updated", "cadence_hours": 5})
    repo.upsert_source(updated)
    assert repo.get_source("rss_ai")["display_name"] == "AI Feed Updated"
    assert repo.get_source("rss_ai")["cadence_hours"] == 5.0

    assert repo.delete_source("rss_ai") is True
    assert repo.get_source("rss_ai") is None
    assert repo.delete_source("rss_ai") is False


NOW = "2026-07-16T10:00:00Z"


def _lifecycle_payload(**overrides):
    payload = {
        "platform": "rss",
        "source_type": "feed",
        "identifier": "https://example.test/lifecycle.xml",
        "display_name": "Lifecycle Feed",
        "default_domain_id": "must-be-cleared",
        "cadence_hours": 24,
        "notes": "baseline",
        "enabled": True,
        "raw_metadata": {
            "max_items_per_run": 10,
            "auto_enrich_media": True,
        },
    }
    return payload | overrides


def _fetch_tasks(base_dir, source_id: str):
    return TaskRepository.for_base_dir(base_dir).list_by_target(
        target_kind="source",
        target_id=source_id,
        task_type=TaskType.FETCH,
    )


def test_create_source_configuration_versions_and_submits_one_initial_fetch(tmp_path) -> None:
    result = create_source_configuration(tmp_path, _lifecycle_payload(), now=NOW)

    source_id = result["source_id"]
    assert result["source"]["source_id"] == source_id
    assert result["source"]["default_domain_id"] is None
    assert result["source_run_id"] is None
    assert result["task_id"]
    assert result["initial_fetch"] is True
    assert result["reused"] is False
    assert len(_fetch_tasks(tmp_path, source_id)) == 1
    versions = SourceRepository(tmp_path).list_versions(source_id, limit=10)
    assert len(versions) == 1
    assert versions[0]["change_reason"] == "created"
    assert versions[0]["config_json"]["display_name"] == "Lifecycle Feed"


def test_create_source_configuration_rejects_duplicate_stable_identity(tmp_path) -> None:
    created = create_source_configuration(tmp_path, _lifecycle_payload(), now=NOW)

    with pytest.raises(DuplicateSourceError) as caught:
        create_source_configuration(
            tmp_path,
            _lifecycle_payload(
                source_id="another-source-id",
                identifier="  https://example.test/lifecycle.xml  ",
                display_name="Duplicate",
            ),
            now=NOW,
        )

    assert caught.value.existing_source_id == created["source_id"]
    assert len(SourceRepository(tmp_path).list_sources()) == 1
    assert len(_fetch_tasks(tmp_path, created["source_id"])) == 1


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"display_name": "Renamed", "notes": "new note"}, "display_changed"),
        ({"cadence_hours": 5}, "schedule_changed"),
        ({"enabled": False}, "activation_changed"),
    ],
)
def test_non_fetch_source_updates_version_without_initial_fetch(
    tmp_path,
    changes: dict,
    reason: str,
) -> None:
    created = create_source_configuration(tmp_path, _lifecycle_payload(), now=NOW)
    source_id = created["source_id"]
    original_fetch_task_id = created["task_id"]
    before = SourceRepository(tmp_path).list_versions(source_id, limit=10)[0]

    result = update_source_configuration(
        tmp_path,
        source_id,
        _lifecycle_payload(**changes),
        now="2026-07-16T11:00:00Z",
    )

    assert result["source_id"] == source_id
    assert result["task_id"] is None
    assert result["source_run_id"] is None
    assert result["initial_fetch"] is False
    assert result["reused"] is False
    assert [task.task_id for task in _fetch_tasks(tmp_path, source_id)] == [original_fetch_task_id]
    versions = SourceRepository(tmp_path).list_versions(source_id, limit=10)
    assert len(versions) == 2
    assert versions[0]["change_reason"] == reason
    assert versions[0]["config_version"] != before["config_version"]
    assert versions[0]["fetch_input_version"] == before["fetch_input_version"]


@pytest.mark.parametrize(
    "metadata_change",
    [
        {"max_items_per_run": 20, "auto_enrich_media": True},
        {"max_items_per_run": 10, "auto_enrich_media": False},
    ],
)
def test_fetch_source_update_versions_and_submits_exactly_one_new_initial_fetch(
    tmp_path,
    metadata_change: dict,
) -> None:
    created = create_source_configuration(tmp_path, _lifecycle_payload(), now=NOW)
    source_id = created["source_id"]
    before = SourceRepository(tmp_path).list_versions(source_id, limit=10)[0]

    result = update_source_configuration(
        tmp_path,
        source_id,
        _lifecycle_payload(
            connector_id="must-not-replace-existing-connector",
            raw_metadata=metadata_change,
        ),
        now="2026-07-16T11:00:00Z",
    )

    assert result["source"]["connector_id"] == source_id
    assert result["source_run_id"] is None
    assert result["task_id"]
    assert result["task_id"] != created["task_id"]
    assert result["initial_fetch"] is True
    assert result["reused"] is False
    assert len(_fetch_tasks(tmp_path, source_id)) == 2
    versions = SourceRepository(tmp_path).list_versions(source_id, limit=10)
    assert len(versions) == 2
    assert versions[0]["change_reason"] == "fetch_config_changed"
    assert versions[0]["config_version"] != before["config_version"]
    assert versions[0]["fetch_input_version"] != before["fetch_input_version"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("platform", "youtube"),
        ("source_type", "keyword"),
        ("identifier", "https://example.test/other.xml"),
    ],
)
def test_update_source_configuration_rejects_identity_changes(
    tmp_path,
    field: str,
    value: str,
) -> None:
    created = create_source_configuration(tmp_path, _lifecycle_payload(), now=NOW)
    source_id = created["source_id"]

    with pytest.raises(ImmutableSourceIdentityError) as caught:
        update_source_configuration(
            tmp_path,
            source_id,
            _lifecycle_payload(**{field: value}),
            now="2026-07-16T11:00:00Z",
        )

    assert field in caught.value.fields
    assert len(SourceRepository(tmp_path).list_versions(source_id, limit=10)) == 1
    assert len(_fetch_tasks(tmp_path, source_id)) == 1


def test_update_source_configuration_raises_explicit_not_found(tmp_path) -> None:
    with pytest.raises(SourceNotFoundError) as caught:
        update_source_configuration(
            tmp_path,
            "missing-source",
            _lifecycle_payload(),
            now=NOW,
        )

    assert caught.value.source_id == "missing-source"


def test_source_activation_versions_state_without_submitting_initial_fetch(tmp_path) -> None:
    created = create_source_configuration(tmp_path, _lifecycle_payload(), now=NOW)
    source_id = created["source_id"]
    existing_task_id = created["task_id"]

    result = set_source_enabled(tmp_path, source_id, False)

    assert result["source_id"] == source_id
    assert result["source"]["enabled"] is False
    assert result["source_run_id"] is None
    assert result["task_id"] is None
    assert result["initial_fetch"] is False
    assert result["reused"] is False
    assert [task.task_id for task in _fetch_tasks(tmp_path, source_id)] == [existing_task_id]
    versions = SourceRepository(tmp_path).list_versions(source_id, limit=10)
    assert len(versions) == 2
    assert versions[0]["change_reason"] == "activation_changed"


def test_source_activation_raises_explicit_not_found(tmp_path) -> None:
    with pytest.raises(SourceNotFoundError):
        set_source_enabled(tmp_path, "missing-source", False)
