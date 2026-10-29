from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

from backend.infrastructure.connectors.base import FetchResult
from backend.domain.models.raw_item import RawAuthor, RawItem, RawTimestamps
from backend.domain.timestamps import normalize_timestamp, utc_now_micro_z
from backend.domain.sources.feed_content import (
    FeedContentMode,
    FeedProvider,
    select_atom_content,
    select_rss2_content,
)


_AUTHOR_PAREN_RE = re.compile(r"\((?P<name>[^)]+)\)")
_CONTENT_ENCODED_NAMESPACES = {"content": "http://purl.org/rss/1.0/modules/content/"}
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_DEFAULT_HEADERS = {"User-Agent": "InfoOS/0.1 (+https://local.infoos)"}


@dataclass
class RSSConnector:
    connector_id: str
    feed_url: str
    data_dir: Path
    source_platform: str = "rss"
    source_type: str = "article"
    max_items_per_run: int | None = None
    feed_provider: FeedProvider = "generic"
    feed_content_mode: FeedContentMode = "feed_preferred"

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        try:
            response = requests.get(self.feed_url, timeout=10, headers=_DEFAULT_HEADERS)
        except TypeError:
            response = requests.get(self.feed_url, timeout=10)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        items = self._feed_items(root)
        newest_first = self._is_newest_first(items)

        raw_items: list[RawItem] = []
        item_pairs = [
            (item, item_id)
            for item in items
            if (item_id := self._item_id(item))
        ]

        if since_cursor is None:
            raw_items = [self._to_raw_item(item, item_id) for item, item_id in item_pairs]
        elif newest_first:
            for item, item_id in item_pairs:
                if item_id == since_cursor:
                    break
                raw_items.append(self._to_raw_item(item, item_id))
        else:
            seen_cursor = False
            for item, item_id in item_pairs:
                if not seen_cursor:
                    if item_id == since_cursor:
                        seen_cursor = True
                    continue
                raw_items.append(self._to_raw_item(item, item_id))

        # Consume the contiguous range immediately after the previous cursor.
        # Taking the newest prefix would jump over the rest of a burst.
        if newest_first and since_cursor is not None:
            raw_items.reverse()
        raw_items = self._limit_items(raw_items)
        next_cursor = self._next_cursor(item_pairs, raw_items, newest_first, since_cursor)
        visible_count = len(item_pairs)
        new_count = len(raw_items)
        return FetchResult(
            items=raw_items,
            next_cursor=next_cursor,
            visible_count=visible_count,
            new_count=new_count,
            dedup_skipped_count=max(0, visible_count - new_count),
        )

    def _to_raw_item(self, item: ET.Element, item_id: str) -> RawItem:
        if self._is_atom_entry(item):
            return self._atom_to_raw_item(item, item_id)

        author_text = self._text(item, "author")
        published_text = self._text(item, "pubDate")
        description = self._text(item, "description")
        title = self._text(item, "title")
        embedded_content = self._text(item, "content:encoded")
        selected = select_rss2_content(
            provider=self.feed_provider,
            mode=self.feed_content_mode,
            content_encoded=embedded_content,
            description=description,
        )
        content = "\n\n".join(part for part in [title, selected.content] if part)
        source_url = self._text(item, "link") or self.feed_url

        return RawItem(
            raw_item_id=item_id,
            connector_id=self.connector_id,
            source_platform=self.source_platform,
            source_type=self.source_type,
            source_url=source_url,
            captured_at=utc_now_micro_z(),
            raw_text=content,
            raw_author=RawAuthor(name=self._parse_author(author_text)),
            raw_timestamps=RawTimestamps(published_at=self._parse_date(published_text)),
            raw_metadata={
                "title": title,
                **({"published_at_original": published_text} if published_text else {}),
                "feed_provider": self.feed_provider,
                "feed_content_mode": self.feed_content_mode,
                "feed_content_origin": selected.origin,
                **_media_metadata(selected.content, source_url),
            },
        )

    def _atom_to_raw_item(self, item: ET.Element, item_id: str) -> RawItem:
        title = self._atom_text(item, "title")
        published_text = self._atom_text(item, "updated") or self._atom_text(item, "published")
        selected = select_atom_content(
            provider=self.feed_provider,
            mode=self.feed_content_mode,
            content=self._atom_text(item, "content"),
            summary=self._atom_text(item, "summary"),
        )
        body = "\n\n".join(part for part in [title, selected.content] if part)
        source_url = self._atom_link(item) or self.feed_url

        return RawItem(
            raw_item_id=item_id,
            connector_id=self.connector_id,
            source_platform=self.source_platform,
            source_type=self.source_type,
            source_url=source_url,
            captured_at=utc_now_micro_z(),
            raw_text=body,
            raw_author=RawAuthor(name=self._atom_author(item)),
            raw_timestamps=RawTimestamps(published_at=self._parse_date(published_text)),
            raw_metadata={
                "title": title,
                **({"published_at_original": published_text} if published_text else {}),
                "feed_provider": self.feed_provider,
                "feed_content_mode": self.feed_content_mode,
                "feed_content_origin": selected.origin,
                **_media_metadata(selected.content, source_url),
            },
        )

    def _parse_author(self, value: str | None) -> str | None:
        if not value:
            return None
        match = _AUTHOR_PAREN_RE.search(value)
        if match:
            return match.group("name")
        return value.strip()

    def _parse_date(self, value: str | None) -> str | None:
        # Unparseable publisher dates stay as raw evidence in raw_metadata.
        return normalize_timestamp(value)

    def _is_newest_first(self, items: list[ET.Element]) -> bool:
        dated_items = [
            parsed
            for item in items
            if (value := self._published_text(item)) and (parsed := self._parse_sort_date(value)) is not None
        ]
        if len(dated_items) < 2:
            return True
        return dated_items[0] >= dated_items[-1]

    def _next_cursor(
        self,
        item_pairs: list[tuple[ET.Element, str]],
        raw_items: list[RawItem],
        newest_first: bool,
        fallback: str | None,
    ) -> str | None:
        if not item_pairs:
            return fallback
        if newest_first and fallback is None and raw_items:
            return raw_items[0].raw_item_id
        if raw_items:
            return raw_items[-1].raw_item_id
        return fallback

    def _limit_items(self, raw_items: list[RawItem]) -> list[RawItem]:
        if self.max_items_per_run is None:
            return raw_items
        limit = max(0, int(self.max_items_per_run))
        if limit == 0:
            return []
        return raw_items[:limit]

    def _text(self, item: ET.Element, tag: str) -> str | None:
        node = item.find(tag, _CONTENT_ENCODED_NAMESPACES)
        if node is None or node.text is None:
            return None
        return node.text.strip()

    def _feed_items(self, root: ET.Element) -> list[ET.Element]:
        atom_entries = root.findall("atom:entry", _ATOM_NS)
        if atom_entries:
            return atom_entries
        return root.findall("./channel/item")

    def _item_id(self, item: ET.Element) -> str | None:
        if self._is_atom_entry(item):
            return self._atom_text(item, "id") or self._atom_link(item) or self._atom_text(item, "title")
        return self._text(item, "guid") or self._text(item, "link") or self._text(item, "title")

    def _published_text(self, item: ET.Element) -> str | None:
        if self._is_atom_entry(item):
            return self._atom_text(item, "updated") or self._atom_text(item, "published")
        return self._text(item, "pubDate")

    def _parse_sort_date(self, value: str) -> datetime | None:
        parsed = self._parse_date(value)
        if not parsed:
            return None
        try:
            return datetime.fromisoformat(parsed.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _is_atom_entry(self, item: ET.Element) -> bool:
        return item.tag == f"{{{_ATOM_NS['atom']}}}entry"

    def _atom_text(self, item: ET.Element, tag: str) -> str | None:
        node = item.find(f"atom:{tag}", _ATOM_NS)
        if node is None or node.text is None:
            return None
        return node.text.strip()

    def _atom_link(self, item: ET.Element) -> str | None:
        links = item.findall("atom:link", _ATOM_NS)
        for node in links:
            if node.attrib.get("rel", "alternate") == "alternate" and node.attrib.get("href"):
                return node.attrib["href"].strip()
        for node in links:
            if node.attrib.get("href"):
                return node.attrib["href"].strip()
        return None

    def _atom_author(self, item: ET.Element) -> str | None:
        node = item.find("atom:author/atom:name", _ATOM_NS)
        if node is None or node.text is None:
            return None
        return node.text.strip()


class _FeedMediaParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.images: list[str] = []
        self.videos: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.casefold() == "img":
            self._append(self.images, values.get("src") or values.get("data-src"))
        elif tag.casefold() in {"video", "source"}:
            self._append(self.videos, values.get("src"))

    def _append(self, target: list[str], value: str | None) -> None:
        if not value:
            return
        resolved = urljoin(self.base_url, value.strip())
        if resolved.startswith(("http://", "https://")) and resolved not in target:
            target.append(resolved)


def _media_metadata(fragment: str | None, base_url: str) -> dict[str, list[str]]:
    if not fragment:
        return {}
    parser = _FeedMediaParser(base_url)
    parser.feed(fragment)
    result: dict[str, list[str]] = {}
    if parser.images:
        result["images"] = parser.images
    if parser.videos:
        result["videos"] = parser.videos
    return result
