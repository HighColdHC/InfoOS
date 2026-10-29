"""Pure keyword-search filters for browser-backed sources."""
from __future__ import annotations

from typing import Any

from backend.domain.sources.registry import platform_definition

PUBLISH_TIME_OPTIONS = ("any", "day", "week", "half-year")


def keyword_search_options(platform: str, raw_metadata: dict[str, Any]) -> dict[str, str]:
    definition = platform_definition(platform)
    if platform not in {"xiaohongshu", "douyin"} or not definition or not definition.research:
        raise ValueError(f"keyword search filters are not supported for {platform}")
    raw = raw_metadata.get("keyword_search", {})
    if not isinstance(raw, dict):
        raise ValueError("keyword_search must be an object")
    options = {}
    for key, default, allowed in (
        ("sort", "general", definition.research.sort_options),
        ("publish_time", "any", PUBLISH_TIME_OPTIONS),
    ):
        value = raw.get(key, default)
        if not isinstance(value, str) or value.strip().lower() not in allowed:
            raise ValueError(f"keyword_search.{key} for {platform} must be one of: {', '.join(allowed)}")
        options[key] = value.strip().lower()
    return options


def keyword_search_command_args(platform: str, raw_metadata: dict[str, Any]) -> list[str]:
    options = keyword_search_options(platform, raw_metadata)
    args = []
    for key, default, flag in (("sort", "general", "--sort"), ("publish_time", "any", "--publish-time")):
        if options[key] != default:
            args.extend([flag, options[key]])
    return args


def keyword_search_result_order(platform: str, raw_metadata: dict[str, Any]) -> str:
    sort = keyword_search_options(platform, raw_metadata)["sort"]
    return "snapshot" if sort.startswith("most-") else "newest_first"
