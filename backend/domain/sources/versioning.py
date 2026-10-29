"""Stable identity for the source configuration that affects collection."""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from typing import Any

from backend.domain.sources.feed_content import versioned_feed_policy
from backend.domain.sources.keyword_search import keyword_search_options


_FETCH_METADATA_KEYS = (
    "feed_url",
    "command",
    "items_path",
    "field_mapping",
    "metadata_mapping",
    "result_order",
    "requires_browser_session",
)


def source_fetch_input_version(source: Mapping[str, Any]) -> str:
    """Return the canonical hash of non-secret, collection-affecting config."""
    raw_metadata = _raw_metadata(source.get("raw_metadata"))
    payload: dict[str, Any] = {
        "platform": _normalized_string(source.get("platform")),
        "source_type": _normalized_string(source.get("source_type")),
        "identifier": _normalized_string(source.get("identifier")),
        "connector": _normalized_string(source.get("connector") or source.get("connector_id")),
        "max_items_per_run": _normalized_integer(
            _source_or_metadata(source, raw_metadata, "max_items_per_run")
        ),
        "auto_enrich_media": _normalized_boolean(
            _source_or_metadata(source, raw_metadata, "auto_enrich_media"),
            default=True,
        ),
    }
    payload.update(
        versioned_feed_policy(
            source.get("platform"),
            source.get("source_type"),
            raw_metadata,
        )
    )
    for key in _FETCH_METADATA_KEYS:
        value = _source_or_metadata(source, raw_metadata, key)
        if key == "requires_browser_session":
            value = _normalized_boolean(value, default=False)
        elif key == "feed_url":
            value = _normalized_string(value)
        payload[key] = value
    if payload["platform"] == "douyin" and payload["source_type"] == "home_feed":
        from backend.domain.sources.douyin_home_feed import home_feed_options
        options = home_feed_options(raw_metadata)
        for key in ("dwell_min_seconds", "dwell_max_seconds"):
            payload[key] = options[key]
        if options["monetization_prefilter"]:
            from backend.domain.sources.monetization_prefilter import adapter_rules
            payload["monetization_prefilter"] = adapter_rules(options)
    if payload["platform"] == "xiaohongshu" and payload["source_type"] == "home_feed":
        from backend.domain.sources.monetization_prefilter import xhs_rules
        rules = xhs_rules(raw_metadata)
        if rules is not None:
            payload["monetization_prefilter"] = rules
    if payload["platform"] in {"xiaohongshu", "douyin"} and payload["source_type"] == "keyword":
        options = keyword_search_options(payload["platform"], raw_metadata)
        if options != {"sort": "general", "publish_time": "any"}:
            payload["keyword_search"] = options
    if payload["platform"] == "zsxq" and payload["source_type"] == "topics":
        from backend.domain.sources.zsxq_recheck import digest_recheck_options, DEFAULT_DAYS, DEFAULT_LIMIT
        options = digest_recheck_options(raw_metadata)
        for key, default in (("digest_recheck_days", DEFAULT_DAYS), ("digest_recheck_limit", DEFAULT_LIMIT)):
            if key in raw_metadata and options[key] != default:
                payload[key] = options[key]
    research = _source_or_metadata(source, raw_metadata, "research")
    if research is not None:
        payload["research"] = research

    detail_command = _source_or_metadata(source, raw_metadata, "detail_command")
    if detail_command is None:
        detail_command = _source_or_metadata(source, raw_metadata, "detail_command_template")
    payload["detail_command"] = detail_command
    if payload["platform"] == "zsxq" and payload["source_type"] == "keyword":
        payload["zsxq_search"] = raw_metadata.get("zsxq_search", raw_metadata.get("scys_search"))

    if (
        payload["platform"] == "douyin"
        and payload["source_type"] == "creator"
        and raw_metadata.get("include_top_comments")
    ):
        payload["include_top_comments"] = True

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _raw_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _source_or_metadata(source: Mapping[str, Any], raw_metadata: dict[str, Any], key: str) -> Any:
    return source[key] if key in source else raw_metadata.get(key)


def _normalized_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip()


def _normalized_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_boolean(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"false", "0", "no", "off", ""}:
            return False
        if normalized in {"true", "1", "yes", "on"}:
            return True
    return bool(value)
