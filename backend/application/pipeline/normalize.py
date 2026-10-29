from __future__ import annotations

import re

from backend.domain.models.normalized_item import NormalizedItem
from backend.domain.models.raw_item import RawItem
from backend.domain.timestamps import normalize_timestamp


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\n\s*\n+", re.MULTILINE)


def normalize_raw_item(raw_item: RawItem) -> NormalizedItem:
    title = raw_item.raw_metadata.get("title") or ""
    raw_text = raw_item.raw_text or ""
    cleaned = _WHITESPACE_RE.sub("\n\n", _TAG_RE.sub("", raw_text)).strip()
    summary = cleaned.split("\n", 1)[0][:180]
    normalized_title = re.sub(r"\s+", " ", str(title)).strip()
    normalized_content = re.sub(r"\s+", " ", cleaned).strip()
    content_quality = "ok"
    if not normalized_content:
        content_quality = "empty"
    elif normalized_content == normalized_title:
        content_quality = "title_only"
    elif len(normalized_content) < 80:
        content_quality = "thin"

    return NormalizedItem(
        raw_item_id=raw_item.raw_item_id,
        source_url=raw_item.source_url,
        title=title,
        normalized_content=cleaned,
        summary=summary,
        published_at=normalize_timestamp(raw_item.raw_timestamps.published_at) if raw_item.raw_timestamps else None,
        source_author=raw_item.raw_author.name if raw_item.raw_author else None,
        content_length=len(cleaned),
        topics=[raw_item.source_platform, raw_item.source_type],
        raw_metadata={
            "source_published_at_raw": raw_item.raw_timestamps.published_at if raw_item.raw_timestamps else None,
            "content_quality": content_quality,
            "content_needs_enrichment": content_quality in {"empty", "title_only", "thin"},
        },
    )
