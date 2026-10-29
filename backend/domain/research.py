"""Pure rules for normalized multi-platform research requests."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.domain.sources.registry import canonical_platform, platform_definition
from backend.domain.sources.zsxq import validate_zsxq_search_options


class ResearchRequestError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def canonical_research_item_identity(
    platform: str,
    raw_item_id: str | None,
    source_url: str | None,
) -> str:
    """Return one stable identity shared by research dedup and card building."""
    item_id = str(raw_item_id or "").strip()
    if item_id and not item_id.lower().startswith(("http://", "https://")):
        return f"id:{platform}:{item_id}"
    candidate_url = str(source_url or item_id).strip()
    return f"url:{platform}:{canonical_research_url(candidate_url)}"


def canonical_research_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    query = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if not _volatile_query_key(key)
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(sorted(query)),
            "",
        )
    )


def _volatile_query_key(value: str) -> bool:
    key = value.casefold()
    return key.startswith("utm_") or key in {
        "apptime",
        "appuid",
        "feature",
        "ignoreengage",
        "ref",
        "referrer",
        "share_from_user_hidden",
        "share_id",
        "share_medium",
        "share_platform",
        "share_red_id",
        "share_source",
        "source",
        "tracking_id",
        "xsec_source",
        "xsec_token",
    }


def normalize_research_request(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise ResearchRequestError(
            "invalid_request",
            "name must contain between 1 and 120 characters",
            details={"field": "name"},
        )

    keywords = _normalized_keywords(payload.get("keywords"))
    date_range = _normalized_date_range(payload.get("date_range"))
    platforms = _normalized_platforms(payload.get("platforms"))
    for item in platforms:
        if item["platform"] == "zsxq":
            try:
                validate_zsxq_search_options(
                    {"zsxq_search": {**item["options"], **(date_range or {}), "keywords": keywords}},
                )
            except ValueError as exc:
                raise ResearchRequestError(
                    "invalid_request", str(exc), details={"platform": "zsxq"},
                ) from exc
    if sum(int(item["max_items"]) for item in platforms) > 5000:
        raise ResearchRequestError(
            "invalid_request",
            "total max_items must not exceed 5000",
            details={"field": "platforms"},
        )
    return {
        "name": name,
        "keywords": keywords,
        "date_range": date_range,
        "platforms": platforms,
    }


def research_request_hash(request: dict[str, Any]) -> str:
    encoded = json.dumps(
        request,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def idempotency_key_hash(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ResearchRequestError(
            "idempotency_key_required",
            "Idempotency-Key header is required",
        )
    if len(normalized) > 200:
        raise ResearchRequestError(
            "invalid_idempotency_key",
            "Idempotency-Key must not exceed 200 characters",
        )
    return f"sha256:{sha256(normalized.encode('utf-8')).hexdigest()}"


def _normalized_keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ResearchRequestError(
            "invalid_request",
            "keywords must be an array",
            details={"field": "keywords"},
        )
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        keyword = str(raw or "").strip()
        if not keyword:
            continue
        if len(keyword) > 100:
            raise ResearchRequestError(
                "invalid_request",
                "each keyword must not exceed 100 characters",
                details={"field": "keywords"},
            )
        identity = keyword.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(keyword)
    if not 1 <= len(result) <= 20:
        raise ResearchRequestError(
            "invalid_request",
            "keywords must contain between 1 and 20 unique values",
            details={"field": "keywords"},
        )
    return result


def _normalized_date_range(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ResearchRequestError(
            "invalid_request",
            "date_range must be an object",
            details={"field": "date_range"},
        )
    try:
        start = date.fromisoformat(str(value.get("start_date") or ""))
        end = date.fromisoformat(str(value.get("end_date") or ""))
    except ValueError as exc:
        raise ResearchRequestError(
            "invalid_request",
            "date_range dates must use YYYY-MM-DD",
            details={"field": "date_range"},
        ) from exc
    if start > end:
        raise ResearchRequestError(
            "invalid_request",
            "start_date must be before or equal to end_date",
            details={"field": "date_range"},
        )
    timezone = str(value.get("timezone") or "").strip()
    if not timezone or len(timezone) > 80:
        raise ResearchRequestError(
            "invalid_request",
            "date_range timezone is required",
            details={"field": "date_range.timezone"},
        )
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ResearchRequestError(
            "invalid_request",
            "date_range timezone must be a valid IANA timezone",
            details={"field": "date_range.timezone"},
        ) from exc
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": timezone,
    }


def _normalized_platforms(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ResearchRequestError(
            "invalid_request",
            "platforms must contain between 1 and 10 entries",
            details={"field": "platforms"},
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ResearchRequestError(
                "invalid_request",
                "each platform entry must be an object",
                details={"field": "platforms"},
            )
        platform = canonical_platform(raw.get("platform") or "")
        definition = platform_definition(platform)
        capability = definition.research if definition is not None else None
        if capability is None:
            raise ResearchRequestError(
                "research_platform_unsupported",
                f"research is not supported for platform: {platform}",
                details={"platform": platform},
            )
        if platform in seen:
            raise ResearchRequestError(
                "invalid_request",
                f"duplicate platform: {platform}",
                details={"field": "platforms"},
            )
        seen.add(platform)
        try:
            max_items = int(raw.get("max_items"))
        except (TypeError, ValueError) as exc:
            raise ResearchRequestError(
                "invalid_request",
                f"max_items is required for platform: {platform}",
                details={"platform": platform},
            ) from exc
        if not 1 <= max_items <= capability.max_items_limit:
            raise ResearchRequestError(
                "invalid_request",
                f"max_items for {platform} must be between 1 and {capability.max_items_limit}",
                details={"platform": platform, "max_items_limit": capability.max_items_limit},
            )
        options = raw.get("options") or {}
        if not isinstance(options, dict):
            raise ResearchRequestError(
                "invalid_request",
                f"options for {platform} must be an object",
                details={"platform": platform},
            )
        _reject_secret_options(options, platform=platform)
        allowed_option_keys = (
            {"source_ids"} if platform == "rss"
            else {"content_type"} if platform == "zsxq" else set()
        )
        unknown_option_keys = sorted(set(options) - allowed_option_keys)
        if unknown_option_keys:
            raise ResearchRequestError(
                "invalid_request",
                f"unsupported options for platform: {platform}",
                details={"platform": platform, "fields": unknown_option_keys},
            )
        source_ids = options.get("source_ids")
        if capability.requires_source_selection and (
            not isinstance(source_ids, list)
            or not source_ids
            or any(not str(item or "").strip() for item in source_ids)
        ):
            raise ResearchRequestError(
                "invalid_request",
                f"{platform} requires options.source_ids",
                details={"platform": platform, "field": "options.source_ids"},
            )
        normalized_options = dict(options)
        if isinstance(source_ids, list):
            normalized_options["source_ids"] = list(
                dict.fromkeys(str(item).strip() for item in source_ids)
            )
        sort = str(raw.get("sort") or capability.default_sort).strip().lower()
        allowed_sorts = capability.sort_options
        if sort not in allowed_sorts:
            raise ResearchRequestError(
                "invalid_request",
                f"sort for {platform} must be {' or '.join(sorted(allowed_sorts))}",
                details={"platform": platform, "field": "sort"},
            )
        result.append(
            {
                "platform": platform,
                "max_items": max_items,
                "sort": sort,
                "options": normalized_options,
            }
        )
    return result


def _reject_secret_options(options: dict[str, Any], *, platform: str) -> None:
    forbidden = {
        "authorization",
        "cookie",
        "cookies",
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
    }
    found = sorted(str(key) for key in options if str(key).casefold() in forbidden)
    if found:
        raise ResearchRequestError(
            "invalid_request",
            "authentication material is not accepted in research requests",
            details={"platform": platform, "fields": found},
        )
