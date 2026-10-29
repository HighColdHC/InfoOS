from __future__ import annotations

import re
from html import unescape
from http.client import HTTPException
from urllib.request import Request, urlopen
from typing import Any

from backend.application.enrichment.media.candidates import trusted_x_relation_media_urls
from backend.application.enrichment.media.x_urls import parse_canonical_x_status_url
from backend.application.enrichment.web_content import _run_opencli_json_command
from backend.application.enrichment.x_blocks._helpers import _upsert_block
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.observability.logging import get_logger

_logger = get_logger("enrichment.x_blocks.repost")


def _enrich_repost(
    card_id: str,
    tweet_id: str,
    source_url: str,
    repo: Any,
    raw_item: RawItem,
) -> list[dict[str, Any]]:
    """Fetch original post content from a retweet/repost plus any media."""
    retweet_payload = _run_opencli_json_command(
        ["opencli", "twitter", "retweet", tweet_id, "--format", "json"],
        timeout=15,
    )
    status: str
    body: str
    provenance: str
    repost_source_url: str | None = None
    quoted_author: dict[str, Any] | None = None
    quoted_at: str | None = None
    media_kind: str | None = None
    metrics: dict[str, Any] | None = None
    public_media_urls: list[str] = []
    relation_media_urls: list[str] = []

    if retweet_payload is None:
        status = "not_found"
        body = ""
        provenance = "No repost data available"
    elif isinstance(retweet_payload, dict):
        relation_media_urls = trusted_x_relation_media_urls(retweet_payload)
        original_text = str(retweet_payload.get("text") or "").strip()
        quoted_author = _author_payload(retweet_payload.get("author"))
        quoted_at = retweet_payload.get("created_at")
        media_kind = _media_kind(retweet_payload)
        metrics = _metrics_payload(retweet_payload)
        if original_text:
            status = "enriched"
            body = original_text
            provenance = "Repost original"
            repost_source_url = str(retweet_payload.get("url") or "")
        else:
            status = "not_found"
            body = ""
            provenance = "No original content in retweet payload"
    elif isinstance(retweet_payload, str):
        body_val = retweet_payload.strip()
        if body_val:
            status = "enriched"
            body = body_val
            provenance = "Repost original (string payload)"
        else:
            status = "not_found"
            body = ""
            provenance = "Retweet payload is empty"
    else:
        status = "not_found"
        body = ""
        provenance = "No repost data available"

    if not body:
        raw_metadata = raw_item.raw_metadata
        opencli_record = raw_metadata.get("opencli_record", {})
        if isinstance(opencli_record, dict):
            retweet = opencli_record.get("retweeted_status") or opencli_record.get("retweeted_tweet") or opencli_record.get("retweet")
            if isinstance(retweet, dict):
                body = str(retweet.get("text") or retweet.get("full_text") or "")
                if body:
                    status = "enriched"
                    provenance = "Repost original (from metadata)"
                    repost_source_url = str(retweet.get("url") or retweet.get("permalink") or repost_source_url or "")
                    quoted_author = quoted_author or _metadata_author_payload(retweet)
                    quoted_at = quoted_at or retweet.get("created_at")
                    media_kind = media_kind or _media_kind(retweet)
                    metrics = metrics or _metrics_payload(retweet)
                    relation_media_urls = relation_media_urls or trusted_x_relation_media_urls(retweet)

    if not body:
        raw_text = (raw_item.raw_text or "").strip()
        public_payload = _fetch_public_x_repost(source_url) if raw_text.startswith("RT @") else None
        if public_payload:
            body = public_payload["text"]
            status = "enriched"
            provenance = "Repost original (from public X page)"
            repost_source_url = public_payload["url"]
            quoted_author = quoted_author or _metadata_author_payload(public_payload)
            quoted_at = quoted_at or public_payload.get("created_at")
            metrics = metrics or _metrics_payload(public_payload)
            public_media_urls = trusted_x_relation_media_urls(public_payload)
            relation_media_urls = relation_media_urls or public_media_urls
            if not media_kind:
                media_kind = _media_kind_from_urls(public_media_urls)
            nested_quote = public_payload["nested_quote"]
        else:
            nested_quote = None
        parsed = _parse_rt_text(raw_text)
        if not body and parsed is not None:
            handle, repost_text = parsed
            status = "enriched"
            provenance = "Repost original (from RT text)"
            body = repost_text
            repost_source_url = repost_source_url or f"https://x.com/{handle}"
            quoted_author = quoted_author or {
                "name": handle,
                "handle": handle,
                "url": f"https://x.com/{handle}",
            }
        elif not body and raw_text.startswith("RT @"):
            status = "not_found"
            provenance = "Repost (original content unavailable)"
            body = raw_text
            nested_quote = None
    else:
        nested_quote = None

    if repost_source_url and provenance == "Repost original (from public X page)":
        thread_payload = _fetch_opencli_thread_post(repost_source_url)
        if thread_payload:
            thread_text = str(thread_payload.get("text") or "").strip()
            if thread_text:
                body = thread_text
                status = "enriched"
                provenance = "Repost original (from OpenCLI thread)"
            repost_source_url = str(thread_payload.get("url") or repost_source_url)
            quoted_author = _metadata_author_payload(thread_payload) or quoted_author
            quoted_at = thread_payload.get("created_at") or quoted_at
            metrics = _metrics_payload(thread_payload) or metrics
            thread_media_urls = trusted_x_relation_media_urls(thread_payload)
            relation_media_urls = relation_media_urls or thread_media_urls
            if thread_media_urls and not public_media_urls:
                public_media_urls = thread_media_urls
            media_kind = media_kind or _media_kind(thread_payload) or _media_kind_from_urls(public_media_urls)
            nested_quote = nested_quote or _nested_quote_payload_from_record(thread_payload.get("quoted_tweet"))

    relation_status = parse_canonical_x_status_url(repost_source_url or "")
    raw_payload: dict[str, Any] = {
        "content_role": "retweeted",
        "relation_depth": 1,
        "relation_source_url": relation_status.url if relation_status else repost_source_url,
        "quoted_author": quoted_author,
        "quoted_at": quoted_at,
        "asset_paths": [],
        "media_kind": media_kind,
        "media_urls": relation_media_urls,
        "metrics": metrics,
        "reposted_by": _reposted_by_payload(raw_item.raw_metadata),
        "nested_quote": nested_quote,
    }

    _upsert_block(
        repo,
        card_id=card_id,
        position=3,
        layer="context",
        block_kind="repost_original",
        status=status,
        body=body,
        source_url=repost_source_url or source_url,
        provenance=provenance,
        raw_payload=raw_payload,
    )
    return [
        {
            "block_id": f"block_{card_id}_repost_original",
            "card_id": card_id,
            "position": 3,
            "layer": "context",
            "block_kind": "repost_original",
            "status": status,
            "body": body,
            "source_url": repost_source_url or source_url,
            "provenance": provenance,
            "raw_payload": raw_payload,
        }
    ]


def _fetch_opencli_thread_post(source_url: str) -> dict[str, Any] | None:
    payload = _run_opencli_json_command(
        ["opencli", "twitter", "thread", source_url, "--limit", "1", "--format", "json", "--window", "background"],
        timeout=30,
    )
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                return item
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _nested_quote_payload_from_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    author = _metadata_author_payload(value)
    text = str(value.get("text") or value.get("full_text") or "").strip()
    url = str(value.get("url") or value.get("permalink") or "").strip()
    if not author or not text:
        return None
    return {
        "author": author,
        "text": text,
        "url": url or None,
    }


def _parse_rt_text(raw_text: str) -> tuple[str, str] | None:
    match = re.match(r"^RT @([A-Za-z0-9_]{1,15}):\s*(.+)$", raw_text.strip(), re.DOTALL)
    if not match:
        return None
    handle = match.group(1)
    body = match.group(2).strip()
    if not body:
        return None
    return handle, body


def _fetch_public_x_repost(source_url: str) -> dict[str, Any] | None:
    if not source_url or "/status/" not in source_url:
        return None
    try:
        request = Request(source_url, headers={"User-Agent": "Mozilla/5.0"})
        html = urlopen(request, timeout=10).read().decode("utf-8", "ignore")
    except (OSError, HTTPException, ValueError):
        _logger.warning("Public X repost page fetch failed: %s", source_url, exc_info=True)
        return None
    if "reposted" not in html:
        return None
    repost_idx = html.find("reposted")
    article_idx = html.find("<article", repost_idx)
    if article_idx < 0:
        return None
    segment = html[article_idx: article_idx + 120000]
    author = _first_author_from_html(segment)
    if author is None:
        return None
    body = _first_text_block(segment)
    if not body:
        return None
    nested_quote = _nested_quote_from_html(segment)
    status_url = _status_url_from_html(segment, author)
    media_urls = _media_urls_from_html(segment)
    return {
        "author": author["handle"],
        "name": author["name"],
        "url": status_url or author["url"],
        "text": body,
        "media_urls": media_urls,
        "nested_quote": nested_quote,
    }


def _status_url_from_html(segment: str, author: dict[str, str]) -> str | None:
    match = re.search(r'<article[^>]+data-tweet-id="(\d+)"', segment)
    handle = author["handle"]
    if not match:
        return None
    return f"https://x.com/{handle}/status/{match.group(1)}"


def _media_urls_from_html(segment: str) -> list[str]:
    quote_start = _nested_quote_start(segment)
    main_segment = segment[:quote_start] if quote_start is not None else segment
    main_video_ids = _video_ids_from_html(main_segment)
    urls: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r'https://pbs\.twimg\.com/media/[^"\\<> ]+', main_segment):
        url = unescape(match.group(0))
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    video_segment = segment if main_video_ids else main_segment
    for match in re.finditer(r'https://video\.twimg\.com/[^"\\<> ]+', video_segment):
        url = unescape(match.group(0))
        if ".mp4" not in url:
            continue
        video_id = _video_id_from_url(url)
        if main_video_ids and video_id not in main_video_ids:
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    videos = [url for url in urls if _media_kind_from_url(url) == "video"]
    images = [url for url in urls if _media_kind_from_url(url) == "image"]
    if videos:
        return [_highest_resolution_video(videos)]
    return images


def _video_ids_from_html(segment: str) -> set[str]:
    ids: set[str] = set()
    for match in re.finditer(r'https://video\.twimg\.com/[^"\\<> ]+', segment):
        video_id = _video_id_from_url(unescape(match.group(0)))
        if video_id:
            ids.add(video_id)
    return ids


def _video_id_from_url(url: str) -> str | None:
    match = re.search(r"video\.twimg\.com/[^/]+/(\d+)/", url)
    return match.group(1) if match else None


def _nested_quote_start(segment: str) -> int | None:
    quote_match = re.search(r'data-href="(/[^"]+/status/[^"]+)"', segment)
    return quote_match.start() if quote_match else None


def _first_author_from_html(segment: str) -> dict[str, str] | None:
    matches = re.finditer(
        r'<a[^>]+href="(?:https://x\.com/|/)([A-Za-z0-9_]{1,15})"[^>]*>(.*?)</a>',
        segment,
        flags=re.DOTALL,
    )
    fallback: dict[str, str] | None = None
    for match in matches:
        handle = match.group(1)
        name = _strip_html(match.group(2))
        candidate = {"name": name or handle, "handle": handle, "url": f"https://x.com/{handle}"}
        fallback = fallback or candidate
        if name and not name.startswith("@"):
            return candidate
    return fallback


def _first_text_block(segment: str) -> str | None:
    match = re.search(r'<div dir="auto"[^>]*>(.*?)</div>', segment, flags=re.DOTALL)
    if not match:
        return None
    text = _strip_html(match.group(1))
    return text or None


def _nested_quote_from_html(segment: str) -> dict[str, Any] | None:
    quote_match = re.search(r'data-href="(/[^"]+/status/[^"]+)"', segment)
    if not quote_match:
        return None
    quote_url = "https://x.com" + unescape(quote_match.group(1))
    quote_segment = segment[quote_match.start(): quote_match.start() + 20000]
    author = _first_author_from_html(quote_segment)
    text = _first_text_block(quote_segment)
    if not author or not text:
        return None
    return {"author": author, "text": text, "url": quote_url}


def _highest_resolution_video(urls: list[str]) -> str:
    def score(url: str) -> int:
        match = re.search(r"/(\d+)x(\d+)/", url)
        if not match:
            return 0
        return int(match.group(1)) * int(match.group(2))

    return max(urls, key=score)


def _strip_html(value: str) -> str:
    value = re.sub(r"<!--\s*-->", "", value)
    value = re.sub(r"<br\s*/?>", "\n", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return value.strip()


def _reposted_by_payload(raw_metadata: dict[str, Any]) -> dict[str, Any] | None:
    record = raw_metadata.get("opencli_record")
    if not isinstance(record, dict):
        return None
    author = record.get("author")
    name = record.get("name")
    if isinstance(author, str) and author.strip():
        handle = author.strip().lstrip("@")
        return {
            "name": str(name or author).strip(),
            "handle": handle,
            "url": f"https://x.com/{handle}",
        }
    return None


def _author_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return {
            "name": value.get("name"),
            "handle": value.get("username") or value.get("handle"),
            "url": value.get("url"),
        }
    if isinstance(value, str) and value.strip():
        handle = value.strip().lstrip("@")
        return {"name": value.strip(), "handle": handle, "url": f"https://x.com/{handle}"}
    return None


def _metadata_author_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    author = payload.get("author")
    name = payload.get("name")
    if isinstance(author, dict):
        return _author_payload(author)
    if isinstance(author, str) and author.strip():
        handle = author.strip().lstrip("@")
        return {
            "name": str(name or author).strip(),
            "handle": handle,
            "url": str(payload.get("author_url") or f"https://x.com/{handle}"),
        }
    if isinstance(name, str) and name.strip():
        return {"name": name.strip(), "handle": None, "url": None}
    return None


def _media_kind(payload: dict[str, Any]) -> str | None:
    explicit = payload.get("media_kind") or payload.get("media_type")
    if explicit:
        return str(explicit)
    media = payload.get("media")
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict):
                candidate = item.get("type") or item.get("media_type")
                if candidate:
                    return str(candidate)
            elif isinstance(item, str):
                candidate = _media_kind_from_url(item)
                if candidate:
                    return candidate
    media_urls = payload.get("media_urls")
    if isinstance(media_urls, list):
        for item in media_urls:
            if isinstance(item, str):
                candidate = _media_kind_from_url(item)
                if candidate:
                    return candidate
    return None


def _metrics_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    value = payload.get("metrics") or payload.get("public_metrics")
    if isinstance(value, dict):
        return value
    metrics = {
        "reply_count": payload.get("reply_count", payload.get("replies")),
        "retweet_count": payload.get("retweet_count", payload.get("retweets")),
        "like_count": payload.get("like_count", payload.get("likes")),
        "view_count": payload.get("view_count", payload.get("views")),
    }
    compact = {key: val for key, val in metrics.items() if val is not None}
    return compact or None


def _media_kind_from_url(url: str) -> str | None:
    value = url.lower()
    if any(token in value for token in (".mp4", ".mov", ".webm", "video.twimg.com")):
        return "video"
    if any(token in value for token in (".jpg", ".jpeg", ".png", ".gif", ".webp", "pbs.twimg.com")):
        return "image"
    return None


def _media_kind_from_urls(urls: list[str]) -> str | None:
    for url in urls:
        kind = _media_kind_from_url(url)
        if kind:
            return kind
    return None
