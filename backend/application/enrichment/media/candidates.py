from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from backend.application.enrichment.media.x_urls import (
    is_x_tweet_media_url,
    is_x_tweet_poster_url,
    parse_canonical_x_status_url,
    x_video_id,
)

MediaKind = Literal["image", "video"]

_MEDIA_URL_KEYS = {
    "image",
    "image_url",
    "image_urls",
    "images",
    "thumbnail",
    "thumbnail_url",
    "cover",
    "cover_url",
    "media_url",
    "media_urls",
    "play_url",
    "video",
    "video_url",
    "video_urls",
    "videos",
    "url",
}
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m3u8"}
_VIDEO_PLATFORMS = {"youtube", "douyin"}
_SOCIAL_MEDIA_PLATFORMS = {"x", "twitter", "reddit", "xiaohongshu"}
_X_RELATION_BLOCK_ROLES = {
    "quote": "quoted",
    "repost_original": "retweeted",
    "reply_parent": "replied_to",
}


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    kind: MediaKind
    source: str
    platform: str
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)


def collect_media_candidates(card: dict[str, Any], base_dir: Path) -> list[MediaCandidate]:
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    from backend.application.enrichment.card_source import card_with_source_payload

    card = card_with_source_payload(card, base_dir)
    platform = card["source_platform"]
    if platform == "x":
        return _collect_trusted_x_candidates(card, base_dir)
    candidates: list[MediaCandidate] = []
    raw_value = card.get("raw_payload")
    raw_payload: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    candidates.extend(_extract_candidates(raw_payload, platform=platform, source="card.raw_payload", priority=10))

    for block in CardContentBlockRepository(base_dir).list_for_card(card["card_id"]):
        block_payload = block["raw_payload"] if isinstance(block["raw_payload"], dict) else {}
        candidates.extend(
            _extract_candidates(
                block_payload,
                platform=platform,
                source=f"block:{block['block_id']}",
                priority=20,
            )
        )
        if block["block_kind"] in {"image", "video"}:
            kind = "image" if block["block_kind"] == "image" else "video"
            url = (block["source_url"] or block["body"]).strip()
            if _is_http_url(url):
                candidates.append(
                    MediaCandidate(
                        url=url,
                        kind=kind,
                        source=f"block:{block['block_id']}",
                        platform=platform,
                        priority=5,
                    )
                )
    return _dedupe_candidates(candidates)


def _collect_trusted_x_candidates(card: dict[str, Any], base_dir: Path) -> list[MediaCandidate]:
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    status = parse_canonical_x_status_url(card["source_url"])
    record = trusted_x_record(card)
    if status is None:
        return []
    found: list[MediaCandidate] = []
    if record is not None:
        media_urls = record.get("media_urls")
        video_ids = {
            video_id
            for url in (media_urls if isinstance(media_urls, list) else [])
            if isinstance(url, str) and (video_id := x_video_id(url)) is not None
        }
        for key in ("media_urls", "media_posters", "media", "images", "videos", "extended_entities", "entities"):
            if key not in record:
                continue
            for candidate in _extract_candidates(
                record[key],
                platform="x",
                source="card.raw_payload.opencli_record",
                priority=10,
                key_hint=key,
            ):
                allowed = (
                    is_x_tweet_poster_url(candidate.url, video_ids)
                    if key == "media_posters"
                    else is_x_tweet_media_url(candidate.url)
                )
                if allowed:
                    found.append(
                        MediaCandidate(
                            url=candidate.url,
                            kind=candidate.kind,
                            source=candidate.source,
                            platform="x",
                            priority=candidate.priority,
                            metadata={"tweet_id": status.tweet_id},
                        )
                    )
    for block in CardContentBlockRepository(base_dir).list_for_card(card["card_id"]):
        relation = _trusted_x_relation(block)
        if relation is None:
            continue
        for candidate in _extract_candidates(
            relation["media_urls"],
            platform="x",
            source=f"block:{block['block_id']}",
            priority=15,
            key_hint="media_urls",
        ):
            if not is_x_tweet_media_url(candidate.url):
                continue
            found.append(
                MediaCandidate(
                    url=candidate.url,
                    kind=candidate.kind,
                    source=candidate.source,
                    platform="x",
                    priority=candidate.priority,
                    metadata={
                        "tweet_id": relation["tweet_id"],
                        "relation_role": relation["role"],
                        "relation_depth": 1,
                        "relation_block_kind": block["block_kind"],
                        "relation_source_url": relation["source_url"],
                    },
                )
            )
    return _dedupe_candidates(found)


def _trusted_x_relation(block: dict[str, Any]) -> dict[str, Any] | None:
    expected_role = _X_RELATION_BLOCK_ROLES.get(block["block_kind"])
    payload = block["raw_payload"]
    if expected_role is None or block["layer"] != "context" or not isinstance(payload, dict):
        return None
    if payload.get("content_role") != expected_role or payload.get("relation_depth") != 1:
        return None
    block_status = parse_canonical_x_status_url(block["source_url"] or "")
    payload_status = parse_canonical_x_status_url(str(payload.get("relation_source_url") or ""))
    if block_status is None or payload_status is None or block_status.tweet_id != payload_status.tweet_id:
        return None
    media_urls = payload.get("media_urls")
    if not isinstance(media_urls, list):
        return None
    return {
        "role": expected_role,
        "tweet_id": block_status.tweet_id,
        "source_url": block_status.url,
        "media_urls": media_urls,
    }


def trusted_x_record(card: dict[str, Any]) -> dict[str, Any] | None:
    status = parse_canonical_x_status_url(card["source_url"])
    if status is None:
        return None
    raw_value = card.get("raw_payload")
    raw_payload: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
    direct = raw_payload.get("opencli_record")
    if isinstance(direct, dict):
        record = direct
    else:
        nested = raw_payload.get("raw_payload")
        record = nested.get("opencli_record") if isinstance(nested, dict) else None
    if not isinstance(record, dict) or _tweet_record_id(record) != status.tweet_id:
        return None
    for key in ("url", "tweet_url", "source_url", "permalink"):
        value = record.get(key)
        if value:
            record_status = parse_canonical_x_status_url(str(value))
            if record_status is None or record_status.tweet_id != status.tweet_id:
                return None
    return record


def trusted_x_relation_media_urls(payload: dict[str, Any]) -> list[str]:
    """Return only public X CDN media URLs from a controlled relation record."""
    found: list[str] = []
    for key in ("media_urls", "media", "images", "videos", "extended_entities", "entities"):
        if key not in payload:
            continue
        for candidate in _extract_candidates(
            payload[key],
            platform="x",
            source="x_relation_record",
            priority=15,
            key_hint=key,
        ):
            if is_x_tweet_media_url(candidate.url) and candidate.url not in found:
                found.append(candidate.url)
    return found


def _tweet_record_id(record: dict[str, Any]) -> str | None:
    for key in ("id", "id_str", "rest_id"):
        value = record.get(key)
        if value is not None and str(value).isdigit():
            return str(value)
    return None


def card_expects_downloadable_media(card: dict[str, Any]) -> bool:
    platform = card["source_platform"]
    source_type = card["source_type"]
    if platform == "reddit":
        return _reddit_expects_downloadable_media(card)
    if platform in _VIDEO_PLATFORMS:
        return True
    if platform in _SOCIAL_MEDIA_PLATFORMS:
        return True
    if source_type in {"video", "social_post"}:
        return True
    return False


def _reddit_expects_downloadable_media(card: dict[str, Any]) -> bool:
    records = _reddit_payload_records(card)
    source_url = card["source_url"].lower()
    if "/gallery/" in source_url or _media_kind(source_url, key_hint="url") is not None:
        return True
    for record in records:
        if _truthy(record.get("is_gallery")):
            return True
        post_hint = str(record.get("post_hint") or "").strip().lower()
        if any(kind in post_hint for kind in ("image", "video", "gallery")):
            return True
        for key in (
            "gallery_urls",
            "video",
            "video_url",
            "media",
            "secure_media",
            "secure_media_embed",
            "images",
            "videos",
            "gallery_data",
        ):
            if _has_media_fact(record.get(key)):
                return True
        if _reddit_media_url_fact(record.get("url_overridden_by_dest")):
            return True
    if any(
        _truthy(record.get("is_self"))
        or bool(str(record.get("selftext") or "").strip())
        or str(record.get("post_hint") or "").strip().lower() in {"self", "text"}
        for record in records
    ):
        return False
    # Unknown Reddit posts stay conservative: only explicit self/text facts can
    # suppress a missing-media gap.
    return True


def _reddit_payload_records(card: dict[str, Any]) -> list[dict[str, Any]]:
    raw_payload = card.get("raw_payload")
    if not isinstance(raw_payload, dict):
        return []
    records = [raw_payload]
    nested = raw_payload.get("raw_payload")
    if isinstance(nested, dict):
        records.append(nested)
    for candidate in tuple(records):
        opencli_record = candidate.get("opencli_record")
        if isinstance(opencli_record, dict):
            records.append(opencli_record)
    return records


def _has_media_fact(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, dict)):
        return bool(value)
    return bool(str(value or "").strip())


def _reddit_media_url_fact(value: Any) -> bool:
    url = str(value or "").strip().lower()
    if not _is_http_url(url):
        return False
    hostname = (urlparse(url).hostname or "").lower()
    return (
        hostname in {"i.redd.it", "v.redd.it", "preview.redd.it", "external-preview.redd.it"}
        or _media_kind(url, key_hint="url") is not None
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _extract_candidates(
    value: Any,
    *,
    platform: str,
    source: str,
    priority: int,
    key_hint: str | None = None,
) -> list[MediaCandidate]:
    if isinstance(value, str):
        url = value.strip()
        kind = _media_kind(url, key_hint=key_hint)
        if kind is None:
            return []
        return [MediaCandidate(url=url, kind=kind, source=source, platform=platform, priority=priority)]
    if isinstance(value, list):
        found: list[MediaCandidate] = []
        for item in value:
            found.extend(_extract_candidates(item, platform=platform, source=source, priority=priority, key_hint=key_hint))
        return found
    if isinstance(value, dict):
        found: list[MediaCandidate] = []
        if platform == "douyin":
            # Prioritize the ordered feed candidates, including extensionless play APIs.
            plays = value.get("play_urls")
            for item in [value.get("play_url"), *(plays if isinstance(plays, list) else [])]:
                if isinstance(item, str):
                    item = "https:" + item if item.startswith("//") else item
                    found.extend(_extract_candidates(item, platform=platform, source=source,
                                                     priority=priority, key_hint="play_url"))
        if isinstance(value.get("url"), str) and isinstance(value.get("type"), str):
            kind = _media_kind(value["url"], key_hint=value["type"])
            if kind is not None:
                found.append(
                    MediaCandidate(
                        url=value["url"].strip(),
                        kind=kind,
                        source=source,
                        platform=platform,
                        priority=priority,
                        metadata={"type": value["type"]},
                    )
                )
        for key, item in value.items():
            hint = str(key).lower()
            if platform == "douyin" and hint in {"play_url", "play_urls"}:
                continue
            if hint in _MEDIA_URL_KEYS or any(part in hint for part in ("image", "video", "media", "thumbnail", "cover", "play")):
                found.extend(_extract_candidates(item, platform=platform, source=source, priority=priority, key_hint=hint))
            elif isinstance(item, (dict, list)):
                found.extend(_extract_candidates(item, platform=platform, source=source, priority=priority, key_hint=hint))
        return found
    return []


def _media_kind(url: str, *, key_hint: str | None) -> MediaKind | None:
    if not _is_http_url(url):
        return None
    hint = (key_hint or "").lower()
    parsed = urlparse(url)
    path = parsed.path.lower()
    suffix = Path(path).suffix
    query_format = parse_qs(parsed.query).get("format", [""])[0].lower()
    if (
        suffix in _IMAGE_EXTENSIONS
        or f".{query_format}" in _IMAGE_EXTENSIONS
        or "image/" in hint
        or any(part in hint for part in ("image", "thumbnail", "cover", "photo"))
    ):
        return "image"
    if suffix in _VIDEO_EXTENSIONS or "video/" in hint or any(part in hint for part in ("video", "play_url")):
        return "video"
    return None


def _is_http_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def _dedupe_candidates(candidates: list[MediaCandidate]) -> list[MediaCandidate]:
    seen: dict[str, int] = {}
    result: list[MediaCandidate] = []
    for candidate in candidates:
        key = f"{candidate.kind}:{candidate.url}"
        if key in seen:
            existing_index = seen[key]
            existing = result[existing_index]
            if candidate.metadata.get("relation_depth") == 1 and existing.metadata.get("relation_depth") != 1:
                result[existing_index] = candidate
            continue
        seen[key] = len(result)
        result.append(candidate)
    return result
