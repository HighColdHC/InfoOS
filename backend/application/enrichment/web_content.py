from __future__ import annotations

import html
import json
import re
import subprocess
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse
from urllib.parse import urljoin

import requests

from backend.application.enrichment.media.artifacts import read_public_url
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.opencli.commands import pin_adapter_lifecycle
from backend.infrastructure.opencli.runtime import resolve_opencli_command
from backend.infrastructure.opencli.tab_lease import normalize_opencli_site, opencli_tab_lease

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\n\s*\n+", re.MULTILINE)
_ARTICLE_BLOCK_RE = re.compile(r"<article\b[^>]*>(?P<body>.*?)</article>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body\b[^>]*>(?P<body>.*?)</body>", re.IGNORECASE | re.DOTALL)
_CONTENT_ENCODED_RE = re.compile(
    r"<content:encoded\b[^>]*><!\[CDATA\[(?P<body>.*?)\]\]></content:encoded>",
    re.IGNORECASE | re.DOTALL,
)
_ARTICLE_FETCH_TIMEOUT_SECONDS = (3.05, 5)
_ARTICLE_MAX_BYTES = 5 * 1024 * 1024
_ARTICLE_USER_AGENT = "InfoOS/0.1 (+https://local.infoos)"


def enrich_raw_item_content(raw_item: RawItem) -> RawItem:
    # The item adapter already fetched the authoritative body, even when short.
    # This pass refetches source_url; it does not follow links in the body.
    if raw_item.raw_metadata.get("capture_adapter") == "github:item":
        return raw_item
    if raw_item.raw_metadata.get("feed_content_mode") == "feed_only":
        return raw_item
    raw_text = (raw_item.raw_text or "").strip()
    title = str(raw_item.raw_metadata.get("title") or "").strip()
    normalized_text = _normalize_inline_text(_clean_html_fragment(raw_text))
    normalized_title = _normalize_inline_text(title)
    is_rss = raw_item.source_platform == "rss"

    if not is_rss and not _needs_enrichment(normalized_text, normalized_title):
        return raw_item

    try:
        enriched_content, media = _fetch_enriched_content_and_media(raw_item)
    except requests.RequestException:
        return raw_item
    if not enriched_content and not (is_rss and media):
        return raw_item

    updated_metadata = dict(raw_item.raw_metadata)
    updated_metadata["enrichment_source_url"] = raw_item.source_url
    updated_metadata["enrichment_applied"] = True
    updated_metadata.update(media)
    return raw_item.model_copy(
        update={"raw_text": enriched_content or raw_item.raw_text, "raw_metadata": updated_metadata}
    )


def _fetch_enriched_content_and_media(raw_item: RawItem) -> tuple[str | None, dict[str, list[str]]]:
    if raw_item.source_platform == "x" or _is_x_url(raw_item.source_url):
        return _fetch_x_expanded_content(raw_item.source_url), {}
    if raw_item.source_platform == "youtube" or _is_youtube_url(raw_item.source_url):
        return _fetch_youtube_transcript(raw_item.source_url), {}
    if not raw_item.source_url:
        return None, {}
    opened = read_public_url(
        raw_item.source_url,
        headers={"User-Agent": _ARTICLE_USER_AGENT},
        timeout=_ARTICLE_FETCH_TIMEOUT_SECONDS,
        max_bytes=_ARTICLE_MAX_BYTES,
    )
    if opened is None:
        return None, {}
    content, final_url = opened
    document = content.decode("utf-8", errors="replace")
    return _extract_content_from_html(document), _extract_media_urls_from_html(document, final_url)


def _needs_enrichment(normalized_text: str, normalized_title: str) -> bool:
    if not normalized_text:
        return True
    if normalized_title and normalized_text == normalized_title:
        return True
    if len(normalized_text) < 80:
        return True
    if normalized_text.lower().endswith("comments"):
        return True
    return False


def _fetch_youtube_transcript(source_url: str) -> str | None:
    video_id = _youtube_video_id(source_url)
    if not video_id:
        return None
    transcript_url = f"https://www.youtube.com/api/timedtext?v={video_id}&fmt=json3&lang=en"
    response = requests.get(transcript_url, timeout=10)
    response.raise_for_status()
    return _extract_transcript_text(response.text)


def _fetch_x_expanded_content(source_url: str) -> str | None:
    tweet_id = _x_tweet_id(source_url)
    if not tweet_id:
        return None

    article_payload = _run_opencli_json_command(["opencli", "twitter", "article", tweet_id, "--format", "json"])
    if isinstance(article_payload, dict):
        article_content = str(article_payload.get("content") or "").strip()
        if article_content:
            return article_content

    thread_payload = _run_opencli_json_command(["opencli", "twitter", "thread", tweet_id, "--format", "json"])
    if isinstance(thread_payload, list):
        lines = []
        for entry in thread_payload:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text") or "").strip()
            if text:
                lines.append(text)
        combined = "\n\n".join(lines).strip()
        if combined:
            return combined
    return None


def _extract_content_from_html(document: str) -> str | None:
    content_match = _CONTENT_ENCODED_RE.search(document)
    if content_match:
        content = _clean_html_fragment(content_match.group("body"))
        if len(_normalize_inline_text(content)) >= 80:
            return content

    article_match = _ARTICLE_BLOCK_RE.search(document)
    if article_match:
        content = _clean_html_fragment(article_match.group("body"))
        if len(_normalize_inline_text(content)) >= 80:
            return content

    body_match = _BODY_RE.search(document)
    if body_match:
        content = _clean_html_fragment(body_match.group("body"))
        if len(_normalize_inline_text(content)) >= 80:
            return content
    return None


class _ArticleMediaParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: list[str] = []
        self.videos: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        normalized = tag.casefold()
        if normalized == "img":
            self._add(self.images, values.get("src") or values.get("data-src"))
        elif normalized in {"video", "source"}:
            self._add(self.videos, values.get("src"))

    def _add(self, target: list[str], value: str | None) -> None:
        if not value:
            return
        resolved = urljoin(self.base_url, value.strip())
        if resolved.startswith(("http://", "https://")) and resolved not in target:
            target.append(resolved)


def _extract_media_urls_from_html(document: str, base_url: str) -> dict[str, list[str]]:
    parser = _ArticleMediaParser(base_url)
    parser.feed(document)
    result: dict[str, list[str]] = {}
    if parser.images:
        result["images"] = parser.images
    if parser.videos:
        result["videos"] = parser.videos
    return result


def _extract_transcript_text(payload_text: str) -> str | None:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return None
    events = payload.get("events")
    if not isinstance(events, list):
        return None
    lines: list[str] = []
    for event in events:
        segs = event.get("segs") if isinstance(event, dict) else None
        if not isinstance(segs, list):
            continue
        parts = []
        for seg in segs:
            text = seg.get("utf8") if isinstance(seg, dict) else None
            if text:
                parts.append(text.replace("\n", " ").strip())
        line = " ".join(part for part in parts if part).strip()
        if line:
            lines.append(line)
    transcript = "\n".join(lines).strip()
    return transcript or None


def _run_opencli_json_command(command: list[str], *, timeout: int = 60) -> dict | list | None:
    site = normalize_opencli_site(command[1])
    resolved_command = resolve_opencli_command([command[0], *pin_adapter_lifecycle(command[1:])])
    with opencli_tab_lease(site):
        result = subprocess.run(
            resolved_command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _clean_html_fragment(fragment: str) -> str:
    without_scripts = _SCRIPT_STYLE_RE.sub(" ", fragment)
    with_breaks = re.sub(r"</(p|div|article|section|li|h1|h2|h3|h4|h5|h6)>", "\n\n", without_scripts, flags=re.IGNORECASE)
    with_breaks = re.sub(r"<br\s*/?>", "\n", with_breaks, flags=re.IGNORECASE)
    text = html.unescape(_TAG_RE.sub("", with_breaks))
    collapsed = _WHITESPACE_RE.sub("\n\n", text).strip()
    lines = [line.strip() for line in collapsed.splitlines()]
    return "\n".join(line for line in lines if line)


def _normalize_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _is_youtube_url(source_url: str) -> bool:
    hostname = urlparse(source_url).netloc.lower()
    return "youtube.com" in hostname or hostname == "youtu.be"


def _is_x_url(source_url: str) -> bool:
    hostname = urlparse(source_url).netloc.lower()
    return hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}


def _youtube_video_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    hostname = parsed.netloc.lower()
    if hostname == "youtu.be":
        return parsed.path.strip("/") or None
    if "youtube.com" not in hostname:
        return None
    query_id = parse_qs(parsed.query).get("v", [])
    if query_id:
        return query_id[0]
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] in {"embed", "shorts", "live"}:
        return path_parts[1]
    return None


def _x_tweet_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if "status" not in path_parts:
        return None
    status_index = path_parts.index("status")
    if status_index + 1 >= len(path_parts):
        return None
    return path_parts[status_index + 1]
