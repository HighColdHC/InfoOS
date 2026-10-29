"""Validation and normalization rules for source configuration."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

from backend.domain.sources.douyin_home_feed import home_feed_options
from backend.domain.sources.douyin import extract_single_douyin_url
from backend.domain.sources.feed_content import validate_feed_policy
from backend.domain.sources.keyword_search import keyword_search_options
from backend.domain.sources.registry import (
    PLATFORM_REGISTRY,
    configurable_platforms,
    platform_definition,
    source_type_capability,
)
from backend.domain.sources.zsxq import validate_zsxq_search_options


PLATFORM_CADENCE_OPTIONS: dict[str, set[float]] = {
    definition.platform: set(definition.cadence_options)
    for definition in PLATFORM_REGISTRY.values()
    if definition.cadence_options
}

SUPPORTED_EXECUTION: set[tuple[str, str]] = {
    (definition.platform, source.source_type)
    for definition in configurable_platforms()
    for source in definition.source_types
    if source.initial_status != "unsupported"
}

DEFAULT_MAX_ITEMS_PER_RUN: dict[tuple[str, str], int] = {
    (definition.platform, source.source_type): source.default_max_items_per_run
    for definition in configurable_platforms()
    for source in definition.source_types
    if source.default_max_items_per_run is not None
}


def normalize_youtube_channel_identifier(identifier: str) -> str:
    value = str(identifier).strip()
    if not value or value.startswith("@") or value.startswith("UC"):
        return value

    url_value = value
    if value.startswith(("youtube.com/", "www.youtube.com/")):
        url_value = f"https://{value}"
    parsed = urlsplit(url_value)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if hostname in {"youtube.com", "m.youtube.com"}:
        parts = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
        if parts and parts[0].startswith("@"):
            return parts[0]
        if len(parts) >= 2 and parts[0] == "channel" and parts[1].startswith("UC"):
            return parts[1]
        return value
    if hostname == "youtu.be":
        return value

    return f"@{value}"


def validate_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    platform = str(payload["platform"])
    source_type = str(payload["source_type"])
    cadence_hours = float(payload["cadence_hours"])
    allowed = PLATFORM_CADENCE_OPTIONS.get(platform)
    if allowed is None:
        raise ValueError(f"unsupported platform: {platform}")
    if platform == "douyin" and source_type in {"keyword", "creator", "item"}:
        allowed = {5, 24}
    if cadence_hours not in allowed:
        raise ValueError(f"cadence {cadence_hours} is not allowed for {platform}")
    identifier = str(payload.get("identifier") or "").strip()
    raw_metadata = payload.get("raw_metadata")
    if platform == "xiaohongshu" and source_type == "home_feed":
        from backend.domain.sources.monetization_prefilter import xhs_rules
        xhs_rules(raw_metadata)
    if platform == "douyin" and source_type == "home_feed":
        options = home_feed_options(raw_metadata)
        raw_metadata = {**(raw_metadata or {}), **options}
        payload = {**payload, "raw_metadata": raw_metadata}
    validate_feed_policy(
        platform,
        source_type,
        raw_metadata if isinstance(raw_metadata, dict) else {},
    )
    source_capability = source_type_capability(platform, source_type)
    if source_capability and source_capability.identifier_options:
        allowed_identifiers = {value for value, _label in source_capability.identifier_options}
        if identifier not in allowed_identifiers:
            allowed_text = ", ".join(sorted(allowed_identifiers))
            raise ValueError(
                f"{platform} {source_type} identifier must be one of: {allowed_text}"
            )
    if platform in {"xiaohongshu", "douyin"} and source_type in {"keyword", "creator", "item"} and not identifier:
        raise ValueError(f"{platform} {source_type} requires identifier")
    if platform == "douyin" and source_type == "item":
        extract_single_douyin_url(identifier)
        raw_metadata = payload.get("raw_metadata")
        if isinstance(raw_metadata, dict):
            raw_metadata = {**raw_metadata, "max_items_per_run": 1}
        payload = {**payload, "max_items_per_run": 1, "raw_metadata": raw_metadata or {"max_items_per_run": 1}}
    if platform == "zsxq" and source_type == "topics":
        from backend.domain.sources.zsxq_recheck import digest_recheck_options
        digest_recheck_options(raw_metadata if isinstance(raw_metadata, dict) else {})
    if platform == "zsxq" and source_type == "keyword" and not identifier:
        raise ValueError("zsxq keyword requires identifier")
    if platform == "zsxq" and source_type == "keyword":
        validate_zsxq_search_options(
            raw_metadata if isinstance(raw_metadata, dict) else {},
            identifier,
        )
    if platform in {"xiaohongshu", "douyin"} and source_type == "keyword":
        keyword_search_options(platform, raw_metadata if isinstance(raw_metadata, dict) else {})
    max_items = int(payload.get("max_items_per_run") or 0)
    if platform == "douyin" and source_type == "home_feed":
        max_items = int(payload.get("max_items_per_run") or raw_metadata.get("max_items_per_run") or 40)
    max_items_limit = (
        source_capability.max_items_limit
        if source_capability and source_capability.max_items_limit
        else (platform_definition(platform).max_items_limit if platform_definition(platform) else 100)
    )
    if max_items and max_items > max_items_limit:
        raise ValueError(
            f"{platform} {source_type} max_items_per_run must be at most {max_items_limit}"
        )
    supported = (platform, source_type) in SUPPORTED_EXECUTION
    enabled = bool(payload.get("enabled", True)) and supported
    return {
        **payload,
        "cadence_hours": cadence_hours,
        "enabled": enabled,
        "connector_status": "implemented_uncalibrated" if supported else "unsupported",
    }


def cadence_to_hours(cadence: str) -> float:
    mapping = {
        "every_15_minutes": 0.25,
        "every_30_minutes": 0.5,
        "hourly": 1.0,
        "every_1_hour": 1.0,
        "every_5_hours": 5.0,
        "every_6_hours": 6.0,
        "daily": 24.0,
        "every_24_hours": 24.0,
    }
    return mapping.get(cadence, 24.0)


def validate_maintained_source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate interactive configuration; legacy imports retain their old projection."""
    platform, source_type = str(payload["platform"]), str(payload["source_type"])
    capability = source_type_capability(platform, source_type)
    if capability is None:
        raise ValueError(f"unsupported source type: {platform} {source_type}")
    # HTTP stores the item limit in metadata; validate that same effective value.
    metadata = payload.get("raw_metadata") or {}
    candidate = {**payload, "max_items_per_run": metadata.get("max_items_per_run", payload.get("max_items_per_run", 0))}
    return validate_source_payload(candidate)
