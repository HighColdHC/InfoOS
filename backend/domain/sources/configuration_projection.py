from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json
from backend.domain.advertising import AD_FIELDS
from backend.domain.sources.douyin_home_feed import home_feed_options
from backend.domain.sources.monetization_prefilter import adapter_rules, xhs_rules
from backend.domain.engagement_metrics import X_ENGAGEMENT_METADATA_MAPPING
from backend.domain.sources.feed_content import feed_policy
from backend.domain.sources.registry import platform_definition, research_default_sort
from backend.domain.sources.keyword_search import keyword_search_command_args, keyword_search_result_order
from backend.domain.sources.configuration_validation import (
    DEFAULT_MAX_ITEMS_PER_RUN,
    normalize_youtube_channel_identifier,
)
from backend.domain.sources.zsxq_recheck import digest_recheck_options
from backend.domain.sources.zsxq_search_cursor import zsxq_search_result_order
from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout
from backend.domain.sources.zsxq import (
    zsxq_group_id,
    zsxq_search_command_args,
    zsxq_tab_keyword,
)

_HACKERNEWS_FEEDS = {"top", "new", "best", "ask", "show", "jobs"}


def database_source_to_connector_config(source: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a SQLite source row to a connector-ready config dict.

    Metadata fields (``source_lane``, ``topics``, ``purpose``, etc.) are
    preserved as pass-through keys so pipeline layers that consume them
    (card builder, source-quality scoring, markdown export) keep working
    regardless of whether the source was loaded from YAML or SQLite.
    """
    connector_id = str(source.get("connector_id") or source["source_id"])
    identifier = str(source.get("identifier") or "")
    platform = source.get("platform")
    source_type = source.get("source_type")
    platform_capability = platform_definition(str(platform))

    base = {
        "source_id": source["source_id"],
        "connector_id": connector_id,
        "enabled": bool(source.get("enabled", True)),
        "requires_browser_session": bool(platform_capability and platform_capability.requires_browser_session),
    }

    # Pull metadata that lives in raw_metadata from the legacy YAML shape.
    # This keeps the contract compatible with the YAML registry's
    # ``to_connector_config`` while making SQLite the storage location.
    raw_meta = source.get("raw_metadata") or {}
    if isinstance(raw_meta, str):
        try:
            import json as _json
            raw_meta = _json.loads(raw_meta)
        except (ValueError, TypeError):
            raw_meta = {}
    metadata_keys = (
        "source_lane",
        "topics",
        "purpose",
        "review_policy",
        "discovery_reason",
        "viewpoint_hint",
        "source_confidence",
        "quality_notes",
        "requires_browser_session",
    )
    for key in metadata_keys:
        if key in raw_meta and raw_meta[key] is not None:
            base[key] = raw_meta[key]

    max_items_per_run = (
        source.get("max_items_per_run")
        or raw_meta.get("max_items_per_run")
        or DEFAULT_MAX_ITEMS_PER_RUN.get((str(platform), str(source_type)))
    )

    if source_type == "research" and source.get("source_scope") == "research":
        research = raw_meta.get("research")
        if not isinstance(research, dict):
            raise ValueError("research source requires a research execution plan")
        platform_request = research.get("platform_request")
        if not isinstance(platform_request, dict):
            raise ValueError("research source requires a platform request")
        return {
            **base,
            "type": "research",
            "source_platform": platform,
            "source_type": "research",
            "research_platform": platform,
            "research_batch_id": research.get("batch_id"),
            "source_scope": "research",
            "keywords": list(research.get("keywords") or []),
            "date_range": research.get("date_range"),
            "max_items_per_run": int(platform_request.get("max_items") or max_items_per_run or 20),
            "sort": str(platform_request.get("sort") or research_default_sort(str(platform))),
            "options": dict(platform_request.get("options") or {}),
            "rss_sources": list(research.get("rss_sources") or []),
        }

    if platform == "rss" and source_type == "feed":
        feed_provider, feed_content_mode = feed_policy(raw_meta)
        return {
            **base,
            "type": "rss",
            "feed_url": identifier,
            "max_items_per_run": max_items_per_run,
            "feed_provider": feed_provider,
            "feed_content_mode": feed_content_mode,
        }

    if source_type == "home_feed":
        limit = str(max_items_per_run or 20)
        browser_base = {
            **base,
            "requires_browser_session": True,
            "max_items_per_run": max_items_per_run,
        }
        if platform == "x":
            return _opencli_config(
                browser_base,
                command=["twitter", "timeline", "--type", identifier, "--limit", limit],
                source_platform="x",
                source_type="social_post",
                result_order="newest_first" if identifier == "following" else "snapshot",
                field_mapping={
                    "id": "id", "title": "text", "url": "url", "text": "text",
                    "author_name": "author", "author_handle": "author",
                    "published_at": "created_at",
                },
                metadata_mapping=dict(X_ENGAGEMENT_METADATA_MAPPING),
            )
        if platform == "youtube":
            return _opencli_config(
                browser_base,
                command=["youtube", "feed", "--limit", limit],
                source_platform="youtube",
                source_type="video",
                result_order="snapshot",
                field_mapping={
                    "id": "url", "title": "title", "url": "url", "text": "title",
                    "author_name": "channel", "published_at": "published",
                },
                metadata_mapping={
                    "rank": "rank", "views": "views", "duration": "duration",
                    "video_id": "video_id",
                },
            )
        if platform == "reddit":
            return _opencli_config(
                browser_base,
                command=["reddit", "home", "--limit", limit],
                source_platform="reddit",
                source_type="social_post",
                result_order="snapshot",
                field_mapping={
                    "id": "postId", "title": "title", "url": "url", "text": "title",
                    "author_name": "author", "author_handle": "author",
                    "published_at": "created_utc",
                },
                metadata_mapping={
                    "subreddit": "subreddit", "score": "score", "comments": "comments",
                    "post_hint": "post_hint", "url_overridden_by_dest": "url_overridden_by_dest",
                    "preview_image_url": "preview_image_url", "gallery_urls": "gallery_urls",
                },
            )
        if platform == "douyin":
            options = home_feed_options(raw_meta)
            prefilter_args = (["--prefilter", json.dumps(adapter_rules(options), ensure_ascii=False, separators=(",", ":"))]
                              if options["monetization_prefilter"] else [])
            return {
                **_opencli_config(
                    browser_base,
                    command=["douyin", "feed", "--limit", limit,
                             "--dwell_min", str(options["dwell_min_seconds"]),
                             "--dwell_max", str(options["dwell_max_seconds"]), "--with_meta", "true", *prefilter_args],
                    source_platform="douyin", source_type="video", result_order="snapshot",
                    field_mapping={
                        "id": "url", "title": "title", "url": "url", "text": "title",
                        "author_name": "author", "author_handle": "author_handle",
                        "author_url": "author_url", "published_at": "published_at",
                    },
                    metadata_mapping={
                        "aweme_id": "aweme_id", "duration": "duration", "likes": "digg_count",
                        "comments": "comment_count", "shares": "share_count", "collects": "collect_count",
                        "play_url": "play_url", "play_urls": "play_urls", "cover_url": "cover_url",
                        "rank": "index", "is_ad": "is_ad", "is_live": "is_live",
                        **{key: key for key in AD_FIELDS},
                        **({"prefilter": "prefilter", "author_signature": "author_signature"} if prefilter_args else {}),
                    },
                ),
                "timeout_seconds": 2100,  # 50 x max(25s + 10s probes, 25s + 8s ad) + advance polling
            }
        if platform == "xiaohongshu":
            rules = xhs_rules(raw_meta)
            config = _opencli_config(
                browser_base,
                command=["xiaohongshu", "feed", "--limit", limit],
                source_platform="xiaohongshu",
                source_type="social_post",
                result_order="snapshot",
                field_mapping={
                    "id": "id", "title": "title", "url": "url", "text": "content",
                    "author_name": "author", "published_at": "published_at",
                },
                metadata_mapping={"likes": "likes", "type": "type"},
                detail_command_template=["xiaohongshu", "note", "{url}"],
                detail_max_items=max_items_per_run or 20,
            )
            if rules is not None:
                config["xhs_prefilter_rules"] = rules
                config["metadata_mapping"].update({key: key for key in ("prefilter", "author_signature", "author_pinned")})
                config["detail_command_template"] = ["xiaohongshu", "screening-note", "{url}"]
            return config

    if platform == "x" and source_type in {"creator", "list"}:
        limit = str(max_items_per_run or 20)
        command = ["twitter", "tweets", identifier, "--limit", limit]
        author_name = "author.name"
        author_handle = "author.username"
        if source_type == "list":
            command = ["twitter", "list-tweets", identifier, "--limit", limit]
            author_name = "author"
            author_handle = "author"

        return {
            **base,
            "type": "opencli",
            "executable": "opencli",
            "command": command,
            "source_platform": "x",
            "source_type": "social_post",
            "result_order": "newest_first",
            "field_mapping": {
                "id": "id",
                "title": "text",
                "url": "url",
                "text": "text",
                "author_name": author_name,
                "author_handle": author_handle,
                "published_at": "created_at",
            },
            "metadata_mapping": dict(X_ENGAGEMENT_METADATA_MAPPING),
        }

    if platform == "youtube" and source_type in {"keyword", "channel"}:
        limit = str(max_items_per_run or 20)
        command = ["youtube", "search", identifier, "--limit", limit, "--sort", "date"]
        if source_type == "channel":
            command = [
                "youtube",
                "channel",
                normalize_youtube_channel_identifier(identifier),
                "--limit",
                limit,
            ]

        return {
            **base,
            "type": "opencli",
            "executable": "opencli",
            "command": command,
            "source_platform": "youtube",
            "source_type": "video",
            "result_order": "newest_first",
            "field_mapping": {
                "id": "url",
                "title": "title",
                "url": "url",
                "text": "title",
                "author_name": "channel",
                "published_at": "published",
            },
        }

    if platform == "reddit" and source_type in {"subreddit", "keyword"}:
        limit = str(max_items_per_run or 20)
        if source_type == "subreddit":
            command = [
                "reddit",
                "subreddit",
                identifier.strip().removeprefix("r/").strip("/"),
                "--sort",
                "new",
                "--limit",
                limit,
            ]
        else:
            command = [
                "reddit",
                "search",
                identifier,
                "--sort",
                "new",
                "--time",
                "week",
                "--limit",
                limit,
            ]
        return _opencli_config(
            {**base, "requires_browser_session": True, "max_items_per_run": max_items_per_run},
            command=command,
            source_platform="reddit",
            source_type="social_post",
            field_mapping={
                "id": "id",
                "title": "title",
                "url": "url",
                "text": "title",
                "author_name": "author",
                "author_handle": "author",
                "published_at": "created_utc",
            },
            metadata_mapping={
                "title": "title",
                "subreddit": "subreddit",
                "selftext": "selftext",
                "score": "score",
                "upvotes": "upvotes",
                "comments": "comments",
                "post_hint": "post_hint",
                "url_overridden_by_dest": "url_overridden_by_dest",
                "preview_image_url": "preview_image_url",
                "gallery_urls": "gallery_urls",
            },
        )

    if platform == "hackernews" and source_type in {"feed", "keyword"}:
        limit = str(max_items_per_run or 20)
        if source_type == "keyword":
            command = ["hackernews", "search", identifier, "--limit", limit, "--sort", "date"]
        else:
            feed = identifier.strip().lower() or "top"
            if feed not in _HACKERNEWS_FEEDS:
                feed = "top"
            command = ["hackernews", feed, "--limit", limit]
        return _opencli_config(
            base,
            command=command,
            source_platform="hackernews",
            source_type="story",
            use_browser_window=False,
            field_mapping={
                "id": "id",
                "title": "title",
                "url": "url",
                "text": "title",
                "author_name": "author",
            },
            metadata_mapping={"title": "title"},
        )

    if platform == "producthunt" and source_type in {"feed", "category"}:
        limit = str(max_items_per_run or 20)
        if source_type == "category":
            command = ["producthunt", "posts", "--category", identifier, "--limit", limit]
        else:
            feed = identifier.strip().lower() or "today"
            command = ["producthunt", "posts" if feed == "posts" else "today", "--limit", limit]
        return _opencli_config(
            base,
            command=command,
            source_platform="producthunt",
            source_type="product_launch",
            use_browser_window=False,
            field_mapping={
                "id": "url",
                "title": "name",
                "url": "url",
                "text": "tagline",
                "author_name": "author",
                "published_at": "date",
            },
            metadata_mapping={"title": "name"},
        )

    if platform == "github" and source_type in {"releases", "commits"}:
        return {
            **base,
            "type": "rss",
            "feed_url": _github_atom_feed_url(identifier, source_type),
            "source_platform": "github",
            "source_type": "release" if source_type == "releases" else "commit",
            "max_items_per_run": max_items_per_run,
        }

    if platform == "xiaohongshu" and source_type in {"keyword", "creator"}:
        return _browser_connector_config(base, platform=platform, source_type=source_type, identifier=identifier, max_items_per_run=max_items_per_run, raw_metadata=raw_meta)

    if platform == "douyin" and source_type == "item":
        return {
            **base,
            "type": "douyin_item",
            "identifier": identifier,
            "source_platform": "douyin",
            "source_type": "item",
            "max_items_per_run": 1,
            "requires_browser_session": True,
        }

    if platform == "douyin" and source_type in {"keyword", "creator"}:
        return _browser_connector_config(base, platform=platform, source_type=source_type, identifier=identifier, max_items_per_run=max_items_per_run, raw_metadata=raw_meta)

    if platform == "zsxq" and source_type in {"topics", "opportunity", "keyword"}:
        return _browser_connector_config(base, platform=platform, source_type=source_type, identifier=identifier, max_items_per_run=max_items_per_run, raw_metadata=raw_meta)

    return {
        **base,
        "type": "unsupported",
        "source_platform": platform,
        "source_type": source_type,
        "unsupported_reason": f"No connector projection for {platform}/{source_type}",
    }


def _opencli_config(
    base: dict[str, Any],
    *,
    command: list[str],
    source_platform: str,
    source_type: str,
    field_mapping: dict[str, str],
    result_order: str = "newest_first",
    use_browser_window: bool = True,
    metadata_mapping: dict[str, str] | None = None,
    detail_command_template: list[str] | None = None,
    detail_max_items: int | None = None,
) -> dict[str, Any]:
    config = {
        **base,
        "type": "opencli",
        "executable": "opencli",
        "command": command,
        "source_platform": source_platform,
        "source_type": source_type,
        "result_order": result_order,
        "use_browser_window": use_browser_window,
        "field_mapping": field_mapping,
        "metadata_mapping": metadata_mapping or {},
    }
    if detail_command_template is not None:
        config["detail_command_template"] = detail_command_template
    if detail_max_items is not None:
        config["detail_max_items"] = detail_max_items
    return config


def _browser_connector_config(
    base: dict[str, Any],
    *,
    platform: str,
    source_type: str,
    identifier: str,
    max_items_per_run: int | None,
    raw_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Pure connector projection for browser-backed source configurations."""
    limit = str(max_items_per_run or 20)
    browser_base = {**base, "requires_browser_session": True, "max_items_per_run": max_items_per_run}
    result_order = "newest_first"
    filter_args = []
    if platform in {"xiaohongshu", "douyin"} and source_type == "keyword":
        filter_args = keyword_search_command_args(platform, raw_metadata)
        result_order = keyword_search_result_order(platform, raw_metadata)
    if platform == "xiaohongshu":
        command = ["xiaohongshu", "user" if source_type == "creator" else "search", identifier, "--limit", limit] + filter_args
        return _opencli_config(
            browser_base, command=command, source_platform=platform, source_type="social_post", result_order=result_order,
            field_mapping={"id": "url", "title": "title", "url": "url", "text": "content", "author_name": "author", "author_url": "author_url", "published_at": "published_at"},
            metadata_mapping={"title": "title", "rank": "rank", "likes": "likes", "collects": "collects", "comments": "comments", "tags": "tags", "type": "type", "note_url": "url", "author": "author", "author_url": "author_url"},
            detail_command_template=["xiaohongshu", "note", "{url}"], detail_max_items=max_items_per_run or 20,
        )
    if platform == "douyin":
        command = ["douyin", "search", identifier, "--limit", limit] + filter_args
        if source_type == "creator":
            include_top_comments = bool(raw_metadata.get("include_top_comments", False))
            comment_limit = max(1, min(50, int(raw_metadata.get("comment_limit") or 50)))
            command = [
                "douyin",
                "user-videos",
                identifier,
                "--limit",
                limit,
                "--max_pages",
                "25",
                "--with_comments",
                "true" if include_top_comments else "false",
                "--comment_limit",
                str(comment_limit),
            ]
            config = _opencli_config(
                browser_base, command=command, source_platform=platform, source_type="video",
                field_mapping={"id": "url", "title": "title", "url": "url", "text": "title", "author_name": "author", "author_handle": "author_handle", "author_url": "author_url", "published_at": "published_at"},
                metadata_mapping={"title": "title", "aweme_id": "aweme_id", "duration": "duration", "likes": "digg_count", "comments": "comment_count", "shares": "share_count", "play_url": "play_url", "cover_url": "cover_url", "top_comments": "top_comments", "author_signature": "author_signature", "follower_count": "follower_count", "following_count": "following_count", "author_aweme_count": "author_aweme_count", "total_favorited": "total_favorited", "author_url": "author_url"},
            )
            config["type"] = "douyin_creator"
            config["timeout_seconds"] = 900
            return config
        return _opencli_config(
            browser_base, command=command, source_platform=platform, source_type="video", result_order=result_order,
            field_mapping={"id": "url", "title": "desc", "url": "url", "text": "desc", "author_name": "author", "published_at": "create_time"},
            metadata_mapping={"title": "desc", "rank": "rank", "plays": "plays", "likes": "likes", "comments": "comments", "shares": "shares", "aweme_id": "aweme_id", "duration": "duration", "play_url": "play_url", "top_comments": "top_comments"},
        )
    group_id = zsxq_group_id(raw_metadata)
    tab = identifier or "all"
    command = ["zsxq", "topics", "--group_id", group_id, "--filter", "all", "--limit", limit]
    if tab != "all":
        command = ["zsxq", "search", zsxq_tab_keyword(tab), "--group_id", group_id, "--content-type", "all", "--limit", limit]
    if source_type == "opportunity":
        command = ["zsxq", "opportunity", "--group_id", group_id, "--limit", limit]
        if tab != "all":
            command.extend(["--keyword", zsxq_tab_keyword(tab)])
    elif source_type == "keyword":
        command = ["zsxq", "search", identifier, "--group_id", group_id, "--limit", limit]
        command.extend(zsxq_search_command_args(identifier, raw_metadata))
        result_order = zsxq_search_result_order(command)
    config = _opencli_config(
        browser_base, command=command, source_platform="zsxq", source_type="article", result_order=result_order,
        field_mapping={"id": "topic_id", "title": "title", "url": "url", "text": "full_text", "author_name": "author", "published_at": "time"},
        metadata_mapping={"title": "title", "topic_id": "topic_id", "type": "type", "tags": "tags", "comments": "comments", "likes": "likes", "readers": "readers", "is_digest": "is_digest", "full_text": "full_text", "links": "links", "images": "images", "videos": "videos", "files": "files", "article_url": "article_url", "article_title": "article_title", "content_source": "content_source", "content_source_status": "content_source_status", "content_source_url": "content_source_url", "content_source_title": "content_source_title", "original_content": "original_content", "content_source_completeness": "content_source_completeness"},
    )
    if source_type == "topics":
        config["digest_recheck"] = digest_recheck_options(raw_metadata)
        config["digest_group_id"] = group_id
    config["timeout_seconds"] = zsxq_command_timeout(command)
    return config


def _github_atom_feed_url(identifier: str, source_type: str) -> str:
    value = identifier.strip().removeprefix("https://github.com/").strip("/")
    owner_repo = "/".join(value.split("/")[:2])
    if not owner_repo or "/" not in owner_repo:
        return identifier
    if source_type == "commits":
        return f"https://github.com/{owner_repo}/commits.atom"
    return f"https://github.com/{owner_repo}/releases.atom"
