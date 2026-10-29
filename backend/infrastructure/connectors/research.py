"""One-shot, multi-keyword research connector implementations."""
from __future__ import annotations

from backend.domain.timestamps import utc_now_micro_z

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from backend.domain.research import canonical_research_item_identity
from backend.domain.sources.registry import research_default_sort
from backend.domain.sources.keyword_search import keyword_search_command_args
from backend.domain.sources.zsxq import validate_zsxq_search_options, zsxq_search_command_args
from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout
from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.infrastructure.connectors.github import github_repository_raw_item
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.connectors.feed.rss import RSSConnector
from backend.infrastructure.connectors.opencli import OpenCLIConnector


@dataclass
class ResearchConnector:
    connector_id: str
    platform: str
    source_id: str
    research_batch_id: str
    keywords: list[str]
    date_range: dict[str, str] | None
    max_items: int
    sort: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    rss_sources: list[dict[str, Any]] = field(default_factory=list)
    data_dir: Path = Path("data")
    coverage_evidence: dict[str, Any] = field(init=False, default_factory=dict)
    discovery_evidence: dict[str, Any] = field(
        init=False,
        default_factory=lambda: {"availability": "unavailable"},
    )

    def __post_init__(self) -> None:
        self.sort = self.sort or research_default_sort(self.platform)

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        del since_cursor
        if self.platform == "github":
            candidates = self._github_candidates()
        elif self.platform == "hackernews":
            candidates = self._hackernews_candidates()
        elif self.platform == "rss":
            candidates = self._rss_candidates()
        elif self.platform == "producthunt":
            candidates = self._producthunt_candidates()
        else:
            candidates = self._opencli_keyword_candidates()
        items, counts = self._merge_filter(candidates)
        visible_by_keyword = Counter(keyword for _item, keyword in candidates)
        accepted_by_keyword = Counter(
            keyword
            for item in items
            for keyword in item.raw_metadata["matched_keywords"]
        )
        limitation = _platform_limitation(self.platform)
        self.coverage_evidence = {
            "platform": self.platform,
            "keywords": list(self.keywords),
            "per_keyword": [
                {
                    "keyword": keyword,
                    "visible_count": int(visible_by_keyword[keyword]),
                    "accepted_count": int(accepted_by_keyword[keyword]),
                }
                for keyword in self.keywords
            ],
            "visible_count": len(candidates),
            "accepted_count": len(items),
            **counts,
            "stop_reason": "max_items_reached" if len(items) >= self.max_items else "provider_exhausted",
            "completeness": "incomplete" if limitation else "bounded_provider_results",
            "limitation": limitation,
        }
        self.discovery_evidence = {
            "executor": _executor_name(self.platform),
            "session_alias": self.platform,
            "sanitized_arguments": list(self.keywords),
            "links": [item.source_url for item in items if item.source_url][:50],
            "discovered_link_count": len(candidates),
            "availability": "available",
        }
        return FetchResult(
            items=items,
            next_cursor=None,
            visible_count=len(candidates),
            new_count=len(items),
            dedup_skipped_count=counts["duplicate_count"] + counts["date_filtered_count"],
        )

    def _opencli_keyword_candidates(self) -> list[tuple[RawItem, str]]:
        if self.platform == "zsxq":
            return self._zsxq_candidates()
        candidates: list[tuple[RawItem, str]] = []
        for keyword in self.keywords:
            config = _opencli_research_config(
                platform=self.platform,
                keyword=keyword,
                limit=self.max_items,
                connector_id=self.connector_id,
                options=self.options,
                date_range=self.date_range,
                sort=self.sort,
            )
            connector = self._opencli_connector(config)
            result = connector.fetch_new_items(since_cursor=None)
            candidates.extend((item, keyword) for item in result.items)
        return candidates

    def _zsxq_candidates(self) -> list[tuple[RawItem, str]]:
        if not self.keywords:
            return []
        config = _opencli_research_config(
            platform="zsxq", keyword=self.keywords[0], limit=self.max_items,
            connector_id=self.connector_id, options={
                **self.options,
                **({"keywords": self.keywords} if len(self.keywords) > 1 else {}),
            },
            date_range=self.date_range, sort=self.sort,
        )
        result = self._opencli_connector(config).fetch_new_items(since_cursor=None)
        return [(item, keyword) for item in result.items for keyword in self.keywords
                if keyword == "*" or keyword.casefold() in (
                    _searchable_text(item) + " " + str(item.raw_metadata.get("tags") or "").casefold()
                )]

    def _producthunt_candidates(self) -> list[tuple[RawItem, str]]:
        config = _opencli_research_config(
            platform="producthunt",
            keyword="",
            limit=min(self.max_items, 50),
            connector_id=self.connector_id,
        )
        result = self._opencli_connector(config).fetch_new_items(since_cursor=None)
        candidates: list[tuple[RawItem, str]] = []
        for item in result.items:
            haystack = _searchable_text(item)
            for keyword in self.keywords:
                if keyword.casefold() in haystack:
                    candidates.append((item, keyword))
        return candidates

    def _rss_candidates(self) -> list[tuple[RawItem, str]]:
        candidates: list[tuple[RawItem, str]] = []
        for source in self.rss_sources:
            connector = RSSConnector(
                connector_id=self.connector_id,
                feed_url=str(source["feed_url"]),
                data_dir=self.data_dir,
                source_platform="rss",
                source_type="article",
                max_items_per_run=self.max_items,
                feed_provider=source.get("feed_provider", "generic"),
                feed_content_mode=source.get("feed_content_mode", "feed_preferred"),
            )
            for item in connector.fetch_new_items(since_cursor=None).items:
                haystack = _searchable_text(item)
                for keyword in self.keywords:
                    if keyword.casefold() in haystack:
                        candidates.append((item, keyword))
        return candidates

    def _github_candidates(self) -> list[tuple[RawItem, str]]:
        candidates: list[tuple[RawItem, str]] = []
        per_keyword = min(self.max_items, 100)
        for keyword in self.keywords:
            payload = self._get_json(
                "https://api.github.com/search/repositories",
                params={
                    "q": keyword,
                    "sort": "updated" if self.sort == "latest" else "stars",
                    "order": "desc",
                    "per_page": per_keyword,
                },
            )
            for record in payload.get("items") or []:
                if isinstance(record, dict):
                    candidates.append((github_repository_raw_item(self.connector_id, record), keyword))
        return candidates

    def _hackernews_candidates(self) -> list[tuple[RawItem, str]]:
        candidates: list[tuple[RawItem, str]] = []
        per_keyword = min(self.max_items, 100)
        for keyword in self.keywords:
            payload = self._get_json(
                "https://hn.algolia.com/api/v1/search_by_date",
                params={
                    "query": keyword,
                    "tags": "story",
                    "hitsPerPage": per_keyword,
                },
            )
            for record in payload.get("hits") or []:
                if isinstance(record, dict):
                    candidates.append((_hackernews_raw_item(self.connector_id, record), keyword))
        return candidates

    def _merge_filter(
        self,
        candidates: list[tuple[RawItem, str]],
    ) -> tuple[list[RawItem], dict[str, int]]:
        merged: dict[str, tuple[RawItem, list[str]]] = {}
        order: list[str] = []
        duplicate_count = 0
        date_filtered_count = 0
        unknown_date_count = 0
        for item, keyword in candidates:
            published = _published_date(
                item,
                timezone_name=(
                    str(self.date_range.get("timezone"))
                    if self.date_range is not None
                    else "UTC"
                ),
            )
            if published is None:
                unknown_date_count += 1
            elif self.date_range is not None and not _date_in_range(published, self.date_range):
                date_filtered_count += 1
                continue
            identity = _item_identity(item)
            if identity in merged:
                duplicate_count += 1
                keywords = merged[identity][1]
                if keyword not in keywords:
                    keywords.append(keyword)
                continue
            merged[identity] = (item, [keyword])
            order.append(identity)
        selected: list[RawItem] = []
        for identity in order[: self.max_items]:
            item, matched = merged[identity]
            selected.append(
                item.model_copy(
                    update={
                        "connector_id": self.connector_id,
                        "raw_metadata": {
                            **item.raw_metadata,
                            "matched_keywords": matched,
                            "research_platform": self.platform,
                            "source_id": self.source_id,
                            "source_scope": "research",
                            "research_batch_id": self.research_batch_id,
                        },
                    }
                )
            )
        return selected, {
            "duplicate_count": duplicate_count,
            "date_filtered_count": date_filtered_count,
            "unknown_date_count": unknown_date_count,
        }

    def _opencli_connector(self, config: dict[str, Any]) -> OpenCLIConnector:
        return OpenCLIConnector(**{**config, "data_dir": self.data_dir})

    def _get_json(self, url: str, *, params: dict[str, Any]) -> dict[str, Any]:
        response = requests.get(
            url,
            params=params,
            timeout=20,
            headers={
                "Accept": "application/json",
                "User-Agent": "InfoOS/0.1 (+https://local.infoos)",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"research provider returned invalid JSON object: {self.platform}")
        return payload


def _opencli_research_config(
    *,
    platform: str,
    keyword: str,
    limit: int,
    connector_id: str,
    options: dict[str, Any] | None = None,
    date_range: dict[str, str] | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    sort = sort or research_default_sort(platform)
    bounded = min(limit, 50 if platform in {"youtube", "producthunt"} else 30 if platform == "douyin" else 100)
    command: list[str]
    source_type = "social_post"
    fields: dict[str, str]
    metadata: dict[str, str] = {}
    use_browser = platform not in {"producthunt"}
    if platform == "x":
        command = ["twitter", "search", keyword, "--limit", str(bounded)]
        fields = {"id": "id", "title": "text", "url": "url", "text": "text", "author_name": "author", "published_at": "created_at"}
    elif platform == "youtube":
        command = ["youtube", "search", keyword, "--limit", str(bounded), "--sort", "date"]
        source_type = "video"
        fields = {"id": "url", "title": "title", "url": "url", "text": "title", "author_name": "channel", "published_at": "published"}
        metadata = {"title": "title", "duration": "duration", "views": "views"}
    elif platform == "reddit":
        command = ["reddit", "search", keyword, "--sort", "new", "--time", "all", "--limit", str(bounded)]
        fields = {"id": "id", "title": "title", "url": "url", "text": "selftext", "author_name": "author", "published_at": "created_utc"}
        metadata = {"title": "title", "subreddit": "subreddit", "score": "score", "comments": "comments"}
    elif platform == "xiaohongshu":
        command = ["xiaohongshu", "search", keyword, "--limit", str(bounded)]
        fields = {"id": "url", "title": "title", "url": "url", "text": "content", "author_name": "author", "published_at": "published_at"}
        metadata = {"title": "title", "likes": "likes", "comments": "comments"}
    elif platform == "douyin":
        command = ["douyin", "search", keyword, "--limit", str(bounded)]
        source_type = "video"
        fields = {"id": "url", "title": "desc", "url": "url", "text": "desc", "author_name": "author", "published_at": "create_time"}
        metadata = {"title": "desc", "likes": "likes", "comments": "comments", "shares": "shares"}
    elif platform == "zsxq":
        command = ["zsxq", "search", keyword, "--group_id", "1824528822", "--limit", str(bounded)]
        search_metadata = {"zsxq_search": {
            **(options or {}),
            "sort": "earliest" if sort == "earliest" else "newest",
            "start_date": (date_range or {}).get("start_date", ""),
            "end_date": (date_range or {}).get("end_date", ""),
        }}
        validate_zsxq_search_options(search_metadata, keyword)
        if options or keyword.strip() == "*" or sort == "earliest":
            command.extend(zsxq_search_command_args(keyword, search_metadata))
        source_type = "article"
        fields = {"id": "topic_id", "title": "title", "url": "url", "text": "full_text", "author_name": "author", "published_at": "time"}
        metadata = {"title": "title", "comments": "comments", "likes": "likes", "readers": "readers", "is_digest": "is_digest", "tags": "tags", "links": "links", "images": "images", "videos": "videos", "files": "files", "article_url": "article_url", "article_title": "article_title", "content_source": "content_source", "content_source_status": "content_source_status", "content_source_url": "content_source_url", "content_source_title": "content_source_title", "original_content": "original_content", "content_source_completeness": "content_source_completeness"}
    elif platform == "producthunt":
        command = ["producthunt", "posts", "--limit", str(bounded)]
        source_type = "product_launch"
        fields = {"id": "url", "title": "name", "url": "url", "text": "tagline", "author_name": "author", "published_at": "date"}
        metadata = {"title": "name"}
    else:
        raise ValueError(f"research connector is not executable for platform: {platform}")
    if platform in {"xiaohongshu", "douyin"}:
        command.extend(keyword_search_command_args(platform, {"keyword_search": {"sort": sort}}))
    config = {
        "connector_id": connector_id,
        "command": command,
        "executable": "opencli",
        "source_platform": platform,
        "source_type": source_type,
        "field_mapping": fields,
        "metadata_mapping": metadata,
        "result_order": ("newest_first" if sort == "latest" else "snapshot") if platform in {"xiaohongshu", "douyin"} else "newest_first",
        "use_browser_window": use_browser,
    }
    if platform == "zsxq":
        config["timeout_seconds"] = zsxq_command_timeout(command)
    elif platform == "x":
        try:
            timeout = int(os.environ.get("INFOOS_X_SEARCH_TIMEOUT_SECONDS", "180"))
        except ValueError:
            timeout = 180
        config["timeout_seconds"] = timeout if timeout > 0 else 180
    return config


def _hackernews_raw_item(connector_id: str, record: dict[str, Any]) -> RawItem:
    object_id = str(record.get("objectID") or _stable_id(record))
    url = str(record.get("url") or f"https://news.ycombinator.com/item?id={object_id}")
    title = str(record.get("title") or record.get("story_title") or "")
    published = str(record.get("created_at") or "") or None
    return RawItem(
        raw_item_id=object_id,
        connector_id=connector_id,
        source_platform="hackernews",
        source_type="story",
        source_url=url,
        captured_at=published or utc_now_micro_z(),
        raw_text=title,
        raw_author=RawAuthor(name=str(record.get("author") or "") or None),
        raw_timestamps=RawTimestamps(published_at=published),
        raw_metadata={
            "title": title,
            "points": record.get("points"),
            "comments": record.get("num_comments"),
            "hackernews_record": record,
        },
    )


def _published_date(item: RawItem, *, timezone_name: str) -> date | None:
    value = item.raw_timestamps.published_at if item.raw_timestamps else None
    if not value:
        return None
    try:
        if str(value).isdigit():
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(ZoneInfo(timezone_name)).date()
    except (OverflowError, ValueError):
        return None


def _date_in_range(value: date, date_range: dict[str, str]) -> bool:
    return date.fromisoformat(date_range["start_date"]) <= value <= date.fromisoformat(date_range["end_date"])


def _item_identity(item: RawItem) -> str:
    return canonical_research_item_identity(
        item.source_platform,
        item.raw_item_id,
        item.source_url,
    )


def _searchable_text(item: RawItem) -> str:
    metadata = item.raw_metadata
    return " ".join(
        [
            str(metadata.get("title") or ""),
            item.raw_text or "",
            item.source_url,
        ]
    ).casefold()


def _stable_id(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:20]




def _executor_name(platform: str) -> str:
    if platform == "github":
        return "github_public_api"
    if platform == "hackernews":
        return "hackernews_algolia"
    if platform == "rss":
        return "selected_rss_sources"
    return "opencli"


def _platform_limitation(platform: str) -> str | None:
    return {
        "rss": "Only the selected maintained RSS feeds were searched.",
        "producthunt": "Only the currently visible Product Hunt feed was filtered; coverage is incomplete.",
        "github": "Only repositories were searched; issues, pull requests and code were excluded.",
        "hackernews": "Results come from the public Algolia story index.",
    }.get(platform)
