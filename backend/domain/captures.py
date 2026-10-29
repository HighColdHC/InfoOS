"""Pure browser-capture normalization and readable-content safety rules."""
from __future__ import annotations

from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


_TRACKING_KEYS = frozenset(
    {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "ref_src", "spm"}
)
_DROP_TAGS = frozenset(
    {"script", "style", "form", "input", "textarea", "select", "option", "button",
     "iframe", "object", "embed", "template", "noscript"}
)
_ALLOWED_TAGS = frozenset(
    {"article", "section", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
     "ul", "ol", "li", "blockquote", "pre", "code", "strong", "em", "b", "i",
     "a", "img", "br", "hr"}
)
_BLOCK_TAGS = frozenset(
    {"article", "section", "p", "div", "h1", "h2", "h3", "h4", "h5", "h6",
     "li", "blockquote", "pre", "br", "hr"}
)
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_NEWLINE_RE = re.compile(r"\n{3,}")


def canonical_capture_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port
    authority = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        authority = f"{hostname}:{port}"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    path = parsed.path or "/"
    return urlunsplit((scheme, authority, path, urlencode(query, doseq=True), ""))


def capture_payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def capture_identity_hash(platform: str, canonical_url: str, *, extended: bool = False) -> str:
    identity = platform_item_identity(platform, canonical_url, include_aliases=extended) or canonical_url
    return sha256(f"{platform}\n{identity}".encode("utf-8")).hexdigest()


def capture_card_url(platform: str, canonical_url: str) -> str:
    identity = platform_item_identity(platform, canonical_url)
    if not identity:
        return canonical_url
    kind, item_id = identity.split(":", 1)
    normalized_platform = platform.lower()
    if normalized_platform == "youtube" and kind == "video":
        return f"https://www.youtube.com/watch?v={item_id}"
    if normalized_platform == "x" and kind == "status":
        return f"https://x.com/i/status/{item_id}"
    if normalized_platform == "reddit" and kind == "comments":
        return f"https://www.reddit.com/comments/{item_id}"
    if normalized_platform == "douyin" and kind == "video":
        return f"https://www.douyin.com/video/{item_id}"
    return canonical_url


def content_identity_hash(text: str, evidence: Any = None) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized_evidence = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) if evidence is not None else ""
    return sha256(
        f"{normalized}\n{normalized_evidence}".encode("utf-8")
    ).hexdigest()


def sanitize_readable_html(fragment: str, *, base_url: str) -> tuple[str, str]:
    parser = _ReadableSanitizer(base_url)
    parser.feed(fragment)
    parser.close()
    html = "".join(parser.html).strip()
    text = _NEWLINE_RE.sub("\n\n", _SPACE_RE.sub(" ", "".join(parser.text))).strip()
    return html, text


def capture_media_urls(fragment: str, *, base_url: str) -> dict[str, list[str]]:
    parser = _CaptureMediaParser(base_url)
    parser.feed(fragment)
    parser.close()
    return {
        **({"images": parser.images} if parser.images else {}),
        **({"videos": parser.videos} if parser.videos else {}),
    }


class _ReadableSanitizer(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.html: list[str] = []
        self.text: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.lower()
        if normalized in _DROP_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth or normalized not in _ALLOWED_TAGS:
            return
        safe_attrs: list[str] = []
        values = {str(key).lower(): value for key, value in attrs}
        if normalized == "a":
            safe = _safe_url(values.get("href"), self.base_url)
            if safe:
                safe_attrs.append(f'href="{escape(safe, quote=True)}"')
        elif normalized == "img":
            safe = _safe_url(values.get("src") or values.get("data-src"), self.base_url)
            if safe:
                safe_attrs.append(f'src="{escape(safe, quote=True)}"')
            alt = str(values.get("alt") or "").strip()[:512]
            if alt:
                safe_attrs.append(f'alt="{escape(alt, quote=True)}"')
        suffix = f" {' '.join(safe_attrs)}" if safe_attrs else ""
        self.html.append(f"<{normalized}{suffix}>")
        if normalized in _BLOCK_TAGS:
            self.text.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in _DROP_TAGS:
            self._drop_depth = max(0, self._drop_depth - 1)
            return
        if self._drop_depth or normalized not in _ALLOWED_TAGS:
            return
        if normalized not in {"img", "br", "hr"}:
            self.html.append(f"</{normalized}>")
        if normalized in _BLOCK_TAGS:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self.html.append(escape(data))
        self.text.append(data)


class _CaptureMediaParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: list[str] = []
        self.videos: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {str(key).lower(): value for key, value in attrs}
        target = self.images if tag.lower() == "img" else (
            self.videos if tag.lower() in {"video", "source"} else None
        )
        if target is None:
            return
        safe = _safe_url(values.get("src") or values.get("data-src"), self.base_url)
        if safe and safe not in target:
            target.append(safe)


def _safe_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None
    resolved = urljoin(base_url, value.strip())
    try:
        parsed = urlsplit(resolved)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return resolved


def platform_item_identity(platform: str, url: str, *, include_aliases: bool = True) -> str | None:
    parsed = urlsplit(url)
    host = str(parsed.hostname or "").lower()
    segments = [segment for segment in parsed.path.split("/") if segment]
    normalized_platform = platform.lower()
    if normalized_platform == "youtube":
        video_id = None
        if host == "youtu.be" and segments:
            video_id = segments[0]
        elif segments[:1] in (["shorts"], ["live"], ["embed"]) and len(segments) > 1:
            video_id = segments[1]
        else:
            video_id = dict(parse_qsl(parsed.query, keep_blank_values=False)).get("v")
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{6,32}", video_id):
            return f"video:{video_id}"
    if normalized_platform == "x" and "status" in segments:
        index = segments.index("status")
        if len(segments) > index + 1 and segments[index + 1].isdigit():
            return f"status:{segments[index + 1]}"
    if normalized_platform == "reddit" and "comments" in segments:
        index = segments.index("comments")
        if len(segments) > index + 1 and re.fullmatch(r"[A-Za-z0-9]+", segments[index + 1]):
            return f"comments:{segments[index + 1].lower()}"
    if normalized_platform == "xiaohongshu":
        for marker in (("explore", "item", "search_result", "note") if include_aliases else ("explore", "item")):
            if marker in segments:
                index = segments.index(marker)
                if len(segments) > index + 1 and re.fullmatch(
                    r"[A-Za-z0-9_-]+", segments[index + 1]
                ):
                    return f"item:{segments[index + 1]}"
    if normalized_platform == "douyin" and "video" in segments:
        index = segments.index("video")
        if len(segments) > index + 1 and segments[index + 1].isdigit():
            return f"video:{segments[index + 1]}"
    return None
