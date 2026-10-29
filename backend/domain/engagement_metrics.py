"""Pure engagement-metric extraction from persisted card payloads."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any


X_ENGAGEMENT_METADATA_MAPPING: Mapping[str, str] = MappingProxyType(
    {
        "views": "views",
        "likes": "likes",
        "replies": "replies",
        "retweets": "retweets",
        "bookmarks": "bookmarks",
        "quotes": "quotes",
    }
)

_DISPLAY_COUNT_MULTIPLIERS = {
    "k": 1_000,
    "m": 1_000_000,
    "b": 1_000_000_000,
    "万": 10_000,
    "亿": 100_000_000,
}


_PLATFORM_METRICS: dict[str, tuple[tuple[str, str], ...]] = {
    "x": (
        ("views", "view_count"),
        ("likes", "like_count"),
        ("replies", "reply_count"),
        ("retweets", "repost_count"),
        ("bookmarks", "save_count"),
        ("quotes", "quote_count"),
    ),
    "douyin": (
        ("plays", "view_count"),
        ("likes", "like_count"),
        ("comments", "comment_count"),
        ("shares", "share_count"),
    ),
    "xiaohongshu": (
        ("likes", "like_count"),
        ("collects", "save_count"),
        ("comments", "comment_count"),
    ),
    "scys": (
        ("like_count", "like_count"),
        ("favorite_count", "save_count"),
        ("comments_count", "comment_count"),
        ("coin_count", "coin_count"),
    ),
    "zsxq": (
        ("likes", "like_count"),
        ("comments", "comment_count"),
        ("readers", "view_count"),
    ),
    "reddit": (
        ("upvotes", "upvote_count"),
        ("comments", "comment_count"),
    ),
    "hackernews": (
        ("score", "score"),
        ("comments", "comment_count"),
    ),
    "youtube": (
        ("views", "view_count"),
        ("likes", "like_count"),
        ("comments", "comment_count"),
    ),
}


def extract_engagement_metrics(platform: str, raw_payload: Any) -> dict[str, int]:
    """Return canonical non-negative counters without mutating the payload."""

    mappings = _PLATFORM_METRICS.get(str(platform or "").strip().casefold(), ())
    if not mappings or not isinstance(raw_payload, dict):
        return {}

    sources = _explicit_sources(raw_payload)
    result: dict[str, int] = {}
    for source_key, canonical_key in mappings:
        for source in sources:
            if source_key not in source:
                continue
            value = _non_negative_int(source[source_key])
            if value is not None:
                result[canonical_key] = value
                break
    return result


def _explicit_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    payloads = [payload]
    nested = payload.get("raw_payload")
    if isinstance(nested, dict):
        payloads.append(nested)

    sources: list[dict[str, Any]] = []
    for candidate in payloads:
        for metrics_key in ("metrics", "public_metrics"):
            metrics = candidate.get(metrics_key)
            if isinstance(metrics, dict):
                sources.append(metrics)
    sources.extend(payloads)

    for candidate in payloads:
        record = candidate.get("opencli_record")
        if not isinstance(record, dict):
            continue
        for metrics_key in ("metrics", "public_metrics"):
            metrics = record.get(metrics_key)
            if isinstance(metrics, dict):
                sources.append(metrics)
        sources.append(record)
    return sources


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        normalized = value.strip().replace("\u00a0", " ").replace("\u202f", " ")
        if normalized.isdecimal():
            return int(normalized)

        scaled = re.fullmatch(
            r"(\d+(?:\.\d+)?)\s*([kKmMbB万亿])(?:\s+\D.*)?",
            normalized,
        )
        if scaled:
            multiplier = _DISPLAY_COUNT_MULTIPLIERS[scaled.group(2).casefold()]
            try:
                count = Decimal(scaled.group(1)) * multiplier
            except InvalidOperation:
                return None
            if count == count.to_integral_value():
                return int(count)

        grouped = re.fullmatch(
            r"(?P<count>\d{1,3}(?P<separator>[, .])\d{3}"
            r"(?:(?P=separator)\d{3})*)(?:\s+[^0-9].*)?",
            normalized,
        )
        if grouped:
            return int(grouped.group("count").replace(grouped.group("separator"), ""))

        displayed_integer = re.fullmatch(r"(\d+)\s+[^0-9].*", normalized)
        if displayed_integer:
            return int(displayed_integer.group(1))
    return None
