"""Pure Feishu URL discovery, hydration reuse, and content-enrich routing."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from backend.domain.feishu_content import feishu_metadata, original_post
from backend.domain.task_queue_policy import CONTENT_ENRICH_BROWSER_PLATFORMS

_FEISHU_URL_RE = re.compile(r"https?://[^\s，,。)）]+(?:feishu\.cn|larksuite\.com)[^\s，,。)）]*")
_EXTERNAL_DOC_HINTS = (
    "飞书好读版",
    "移步飞书",
    "完整内容在飞书",
    "核心内容在飞书",
    "飞书文档",
)


def external_doc_urls(card: dict[str, Any], blocks: list[dict[str, Any]], *, document_url: str = "") -> list[str]:
    existing = []
    for block in blocks:
        payload = block.get("raw_payload") or {}
        if block.get("block_kind") == "external_doc" and payload.get("provider") == "feishu":
            existing.extend(str(payload.get(key) or "") for key in ("document_url", "resolved_url"))
    return _ordered_unique([
        *([document_url] if _is_feishu_url(document_url) else []),
        *_card_urls(card), *_find_feishu_urls(_card_text(card)),
        *(url for url in existing if _is_feishu_url(url)),
    ])


def has_external_doc_hint(card: dict[str, Any]) -> bool:
    return any(hint in _card_text(card) for hint in _EXTERNAL_DOC_HINTS)


def fetch_hydration(raw: dict[str, Any] | None, url: str,
                    blocks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not raw:
        return None
    metadata = raw.get("raw_metadata") or {}
    evidence = feishu_metadata(metadata)
    if not (evidence.get("content_source_status") == "fetched"
            and evidence.get("content_source_completeness") == "complete"):
        return None
    resolved = str(evidence.get("content_source_url") or "").strip()
    if not _is_feishu_url(resolved):
        return None
    matches = url == resolved or any(
        block.get("block_kind") == "external_doc"
        and (block.get("raw_payload") or {}).get("provider") == "feishu"
        and (block.get("raw_payload") or {}).get("document_url") == url
        and (block.get("raw_payload") or {}).get("resolved_url") == resolved
        for block in blocks
    )
    if not matches:
        return None
    record = metadata.get("opencli_record") or {}
    # A later published read owns raw_text; the old OpenCLI body may be partial.
    published = isinstance(metadata.get("external_doc_read"), dict)
    body = "" if published else str(record.get("external_doc_body") or metadata.get("external_doc_body") or "").strip()
    if not body:
        body = str(raw.get("raw_text") or record.get("full_text") or metadata.get("full_text") or "").strip()
        post = original_post(metadata)
        if post and body.startswith(post + "\n\n"):
            body = body[len(post):].strip()
        elif post and body == post:
            body = ""
    if not body:
        return None
    # Never borrow another document's media when a later read changed the URL.
    media = record if record.get("content_source_url") == resolved else metadata
    return {
        "source_url": resolved, "body": body,
        "blocks": evidence.get("content_source_blocks", evidence.get("blocks", record.get("content_source_blocks", 0))),
        "reached_end": evidence.get("content_source_reached_end", evidence.get("reached_end", record.get("content_source_reached_end", False))),
        "images": media.get("images"), "videos": media.get("videos"),
        "media_blobs": media.get("media_blobs"),
    }


def content_enrich_needs_browser(card: dict[str, Any], raw: dict[str, Any] | None,
                                blocks: list[dict[str, Any]], *, force_browser: bool = False) -> bool:
    platform = str(card.get("source_platform") or "").strip().lower()
    if platform not in {"zsxq", "scys"}:
        return platform in CONTENT_ENRICH_BROWSER_PLATFORMS
    from backend.domain.zsxq_article import article_gap

    if article_gap(card):
        return True
    if force_browser:
        return True
    urls = external_doc_urls(card, blocks)
    return bool(urls and fetch_hydration(raw, urls[0], blocks) is None)


def _card_text(card: dict[str, Any]) -> str:
    raw_payload = card.get("raw_payload") if isinstance(card.get("raw_payload"), dict) else {}
    parts = [
        str(card.get("title") or ""),
        str(card.get("content") or ""),
        str(card.get("summary") or ""),
        str(raw_payload.get("full_text") or ""),
        str(raw_payload.get("normalized_content") or ""),
    ]
    return "\n".join(part for part in parts if part.strip())


def _find_feishu_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in _FEISHU_URL_RE.finditer(text):
        url = match.group(0).rstrip("，,。.;；)")
        if _is_feishu_url(url) and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def _card_urls(card: dict[str, Any]) -> list[str]:
    payload = card.get("raw_payload")
    if not isinstance(payload, dict):
        return []
    found: list[str] = []
    stack: list[Any] = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, nested in reversed(list(value.items())):
                if key in {"links", "link", "href", "external_urls", "content_source_url", "document_url"}:
                    found.extend(_url_values(nested))
                elif isinstance(nested, (dict, list)):
                    stack.append(nested)
        elif isinstance(value, list):
            stack.extend(reversed(value))
    return [url for url in _ordered_unique(found) if _is_feishu_url(url)]


def _url_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str)]
    return []


def _is_feishu_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and any(hostname == domain or hostname.endswith(f".{domain}") for domain in ("feishu.cn", "larksuite.com"))
    )


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip().rstrip("，,。.;；)")
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


