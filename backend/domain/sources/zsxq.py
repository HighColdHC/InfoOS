"""Pure ZSXQ/Shengcai source search configuration rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
import json
from typing import Any


ZSXQ_DEFAULT_GROUP_ID = "1824528822"
ZSXQ_TAB_KEYWORDS = {
    "ai": "AI",
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "gongzhonghao": "公众号",
}
ZSXQ_SEARCH_SORTS = {
    "comprehensive", "newest", "earliest", "most-favorited",
    "most-commented", "most-liked", "most-coins", "recent-coins",
}
ZSXQ_SEARCH_CONTENT_TYPES = {
    "all", "essence", "bid", "topic", "opportunity", "square",
}


def zsxq_group_id(raw_metadata: dict[str, Any]) -> str:
    value = str(raw_metadata.get("group_id") or ZSXQ_DEFAULT_GROUP_ID).strip()
    return value or ZSXQ_DEFAULT_GROUP_ID


def zsxq_tab_keyword(tab: str) -> str:
    return ZSXQ_TAB_KEYWORDS.get(str(tab).strip(), str(tab).strip())


def zsxq_search_command_args(identifier: str, raw_metadata: dict[str, Any]) -> list[str]:
    options = _search_options(raw_metadata)
    keywords = zsxq_search_keywords(identifier, raw_metadata)
    configured = _raw_search(raw_metadata).get("keywords", [])
    has_configured_keywords = isinstance(configured, list) and any(
        str(value).strip() for value in configured
    )
    arguments = ["--sort", options["sort"], "--content-type", options["content_type"]]
    if has_configured_keywords or len(keywords) > 1:
        arguments.extend(["--keywords-json", json.dumps(keywords, ensure_ascii=False, separators=(",", ":"))])
    if options["start_date"]:
        arguments.extend(["--start-date", options["start_date"]])
    if options["end_date"]:
        arguments.extend(["--end-date", options["end_date"]])
    return arguments


def validate_zsxq_search_options(raw_metadata: dict[str, Any], identifier: str = "") -> None:
    options = _search_options(raw_metadata)
    if options["sort"] not in ZSXQ_SEARCH_SORTS:
        raise ValueError(f"unsupported zsxq search sort: {options['sort']}")
    if options["content_type"] not in ZSXQ_SEARCH_CONTENT_TYPES:
        raise ValueError(f"unsupported zsxq search content_type: {options['content_type']}")
    raw = _raw_search(raw_metadata)
    configured = raw_metadata.get("zsxq_search", raw_metadata.get("scys_search"))
    if configured is not None and not isinstance(configured, dict):
        raise ValueError("zsxq search configuration must be an object")
    keywords = raw.get("keywords", [])
    if not isinstance(keywords, list) or any(not isinstance(value, str) for value in keywords):
        raise ValueError("zsxq search keywords must be a list of strings")
    search_keywords = zsxq_search_keywords(identifier, raw_metadata)
    if identifier.strip() == "*" or "*" in search_keywords:
        if options["content_type"] not in {"all", "essence"}:
            raise ValueError("zsxq wildcard keyword * can only be used with all (全部) or essence (精华)")
        if not options["start_date"]:
            raise ValueError("zsxq wildcard keyword * requires start_date")
    if len(search_keywords) > 20:
        raise ValueError("zsxq search supports at most 20 keywords")
    parsed_dates: dict[str, date] = {}
    for key in ("start_date", "end_date"):
        value = options[key]
        if not value:
            continue
        try:
            parsed_dates[key] = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"zsxq search {key} must use YYYY-MM-DD") from error
    if parsed_dates.get("start_date") and parsed_dates.get("end_date") and parsed_dates["start_date"] > parsed_dates["end_date"]:
        raise ValueError("zsxq search start_date must not be later than end_date")


def zsxq_search_keywords(identifier: str, raw_metadata: dict[str, Any]) -> list[str]:
    configured = _raw_search(raw_metadata).get("keywords", [])
    configured_candidates = configured if isinstance(configured, list) else []
    candidates = (
        configured_candidates
        if any(str(value).strip() for value in configured_candidates)
        else [identifier]
    )
    keywords: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = str(candidate).strip()
        identity = value.casefold()
        if value and identity not in seen:
            seen.add(identity)
            keywords.append(value)
    return keywords


def _raw_search(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    raw = raw_metadata.get("zsxq_search", raw_metadata.get("scys_search"))
    return raw if isinstance(raw, dict) else {}


def _search_options(raw_metadata: dict[str, Any]) -> dict[str, str]:
    values = _raw_search(raw_metadata)
    return {
        "sort": str(values.get("sort") or "comprehensive").strip().lower(),
        "content_type": str(values.get("content_type") or "all").strip().lower(),
        "start_date": str(values.get("start_date") or "").strip(),
        "end_date": str(values.get("end_date") or "").strip(),
    }


def opportunity_source_for_topics(source: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]) -> str | None:
    """An enabled opportunity(all) source owns tagged posts in the same group."""
    if source["platform"] != "zsxq" or source["source_type"] != "topics":
        return None
    group_id = zsxq_group_id(source["raw_metadata"])
    for other in sources:
        if (other["platform"] == "zsxq" and other["source_type"] == "opportunity"
                and other["enabled"] and other["identifier"] in {"", "all"}
                and zsxq_group_id(other["raw_metadata"]) == group_id):
            return other["source_id"]
    return None
