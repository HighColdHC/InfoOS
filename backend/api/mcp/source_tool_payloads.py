"""Adapt agent partial inputs to the same payload contract used by Web."""
from typing import Any

from backend.api.schemas.sources import SourcePayload, source_payload_dict
from backend.domain.sources.configuration import cadence_to_hours
from backend.domain.sources.registry import source_type_capability

_OPTION_FIELDS = {"cadence_hours", "max_items_per_run", "enabled", "notes", "raw_metadata"}
_EDIT_FIELDS = _OPTION_FIELDS | {"display_name", "platform", "source_type", "identifier"}
_METADATA_FIELDS = {
    "auto_enrich_media", "feed_provider", "feed_content_mode", "ai_processing",
    "include_top_comments", "keyword_search", "zsxq_search", "digest_recheck_days", "digest_recheck_limit",
    "monetization_prefilter", "dwell_min_seconds", "dwell_max_seconds",
    "session_interval_min_minutes", "session_interval_max_minutes", "active_hours",
    "prefilter_comment_min_count", "prefilter_demand_min_comments",
}


def _check_fields(values: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(values) - allowed
    if unknown:
        raise ValueError("不允许修改字段：" + ", ".join(sorted(unknown)))
    metadata = values.get("raw_metadata")
    if "raw_metadata" in values:
        if not isinstance(metadata, dict):
            raise ValueError("raw_metadata 必须为对象")
        unknown = set(metadata) - _METADATA_FIELDS
        if unknown:
            raise ValueError("不允许修改来源内部元数据：" + ", ".join(sorted(unknown)))


def create_payload(platform: str, source_type: str, identifier: str,
                   name: str | None, options: dict[str, Any] | None) -> dict[str, Any]:
    options = options or {}
    _check_fields(options, _OPTION_FIELDS)
    platform, source_type = platform.strip(), source_type.strip()
    capability = source_type_capability(platform, source_type)
    body = SourcePayload.model_validate({
        "platform": platform, "source_type": source_type, "identifier": identifier,
        "display_name": name if name is not None else identifier,
        "cadence_hours": cadence_to_hours(capability.default_cadence) if capability else 24,
        "max_items_per_run": (capability.default_max_items_per_run or 20) if capability else 20,
        **options,
    })
    return source_payload_dict(body)


def update_payload(existing: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    _check_fields(changes, _EDIT_FIELDS)
    metadata = existing["raw_metadata"]
    merged = {**existing, "max_items_per_run": metadata.get("max_items_per_run", 20), **changes}
    merged["raw_metadata"] = {**metadata, **changes.get("raw_metadata", {})}
    body = SourcePayload.model_validate(merged)
    # Only forward the changed fields: the use case merges against its locked current row.
    payload = source_payload_dict(body)
    result = {key: payload[key] for key in changes if key != "max_items_per_run"}
    if "raw_metadata" in changes:
        result["raw_metadata"] = {key: payload["raw_metadata"][key] for key in changes["raw_metadata"]}
    if "max_items_per_run" in changes:
        result.setdefault("raw_metadata", {})["max_items_per_run"] = body.max_items_per_run
    return result
