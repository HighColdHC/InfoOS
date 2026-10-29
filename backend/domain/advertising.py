"""Normalized public advertising metadata, without I/O or platform calls."""
from __future__ import annotations

import json
from typing import Any, NotRequired, TypedDict, cast


class AdMetadata(TypedDict):
    ad_id: str
    ad_creative_id: str
    ad_title: str
    ad_button_text: str
    ad_landing_url: str
    ad_web_url: str
    ad_kind: str
    ad_advertiser_id: str
    ad_live_room_id: str
    ad_group_type: int
    ad_source_type: int
    is_ad: bool
    ad_is_soft: bool


class AdvertisingProjection(TypedDict):
    ad: NotRequired[AdMetadata]


AD_STRING_FIELDS = (
    "ad_id", "ad_creative_id", "ad_title", "ad_button_text", "ad_landing_url",
    "ad_web_url", "ad_kind", "ad_advertiser_id", "ad_live_room_id",
)
AD_NUMBER_FIELDS = ("ad_group_type", "ad_source_type")
AD_FIELDS = (*AD_STRING_FIELDS, *AD_NUMBER_FIELDS, "ad_is_soft")


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _boolean(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true"}


def advertising_projection(payload: Any) -> AdvertisingProjection:
    """Return an optional ad object from card/raw/OpenCLI metadata shapes."""
    root = _object(payload)
    record = dict(root)
    for key in ("raw_payload", "raw_metadata", "metadata", "opencli_record"):
        nested = _object(record.get(key))
        record.update(nested)
    if not _boolean(record.get("is_ad")):
        return {}
    ad = {key: str(record.get(key) or "") for key in AD_STRING_FIELDS}
    for key in AD_NUMBER_FIELDS:
        try:
            ad[key] = int(record.get(key) or 0)
        except (TypeError, ValueError, OverflowError):
            ad[key] = 0
    ad["is_ad"] = True
    ad["ad_is_soft"] = _boolean(record.get("ad_is_soft"))
    return {"ad": cast(AdMetadata, ad)}
