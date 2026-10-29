from __future__ import annotations

import re
from typing import Any

import requests

from backend.application.enrichment.web_content import _extract_content_from_html
from backend.application.enrichment.x_blocks._helpers import _is_x_url, _upsert_block
from backend.domain.models.raw_item import RawItem

_TCO_URL_RE = re.compile(r"https://t\.co/\w+")
_TCO_TIMEOUT = (3.05, 10)
_TCO_RESOLVE_TIMEOUT = 10


def _enrich_tco_links(
    card_id: str,
    raw_item: RawItem,
    repo: Any,
) -> list[dict[str, Any]]:
    """Resolve the first t.co link in tweet text and fetch the linked article. Returns list of block dicts."""
    raw_text = raw_item.raw_text or ""
    tco_urls = _TCO_URL_RE.findall(raw_text)

    if not tco_urls:
        return []  # No t.co links — skip silently

    # Only resolve the first t.co link
    tco_url = tco_urls[0]
    status: str
    body: str
    resolved_url: str = tco_url

    try:
        # Resolve the t.co URL to get the final destination
        head_response = requests.head(
            tco_url,
            allow_redirects=True,
            timeout=_TCO_RESOLVE_TIMEOUT,
        )
        resolved_url = head_response.url
    except requests.RequestException:
        status = "blocked"
        body = ""
        _upsert_block(
            repo,
            card_id=card_id,
            position=5,
            layer="context",
            block_kind="linked_article",
            status=status,
            body=body,
            source_url=tco_url,
            provenance="Could not resolve t.co link",
        )
        return [
            {
                "block_id": f"block_{card_id}_linked_article",
                "card_id": card_id,
                "position": 5,
                "layer": "context",
                "block_kind": "linked_article",
                "status": status,
                "body": body,
                "source_url": tco_url,
                "provenance": "Could not resolve t.co link",
            }
        ]

    # Check if resolved URL is another X/twitter URL
    if _is_x_url(resolved_url):
        _upsert_block(
            repo,
            card_id=card_id,
            position=5,
            layer="context",
            block_kind="linked_article",
            status="unsupported",
            body="",
            source_url=resolved_url,
            provenance="链接指向 X 平台内容（非外部文章）",
        )
        return [
            {
                "block_id": f"block_{card_id}_linked_article",
                "card_id": card_id,
                "position": 5,
                "layer": "context",
                "block_kind": "linked_article",
                "status": "unsupported",
                "body": "",
                "source_url": resolved_url,
                "provenance": "链接指向 X 平台内容（非外部文章）",
            }
        ]

    # Fetch article body
    try:
        response = requests.get(resolved_url, timeout=_TCO_TIMEOUT)
        response.raise_for_status()
        article_body = _extract_content_from_html(response.text)
    except requests.RequestException:
        status = "blocked"
        body = ""
        _upsert_block(
            repo,
            card_id=card_id,
            position=5,
            layer="context",
            block_kind="linked_article",
            status=status,
            body=body,
            source_url=resolved_url,
            provenance="Linked article fetch failed",
        )
        return [
            {
                "block_id": f"block_{card_id}_linked_article",
                "card_id": card_id,
                "position": 5,
                "layer": "context",
                "block_kind": "linked_article",
                "status": status,
                "body": body,
                "source_url": resolved_url,
                "provenance": "Linked article fetch failed",
            }
        ]

    if article_body:
        # Limit body to ~2000 chars for budget
        body = article_body[:2000]
        status = "body_fetched"
        provenance = "Linked article from tweet"
    else:
        body = ""
        status = "not_found"
        provenance = "No extractable article content"

    _upsert_block(
        repo,
        card_id=card_id,
        position=5,
        layer="context",
        block_kind="linked_article",
        status=status,
        body=body,
        source_url=resolved_url,
        provenance=provenance,
    )
    return [
        {
            "block_id": f"block_{card_id}_linked_article",
            "card_id": card_id,
            "position": 5,
            "layer": "context",
            "block_kind": "linked_article",
            "status": status,
            "body": body,
            "source_url": resolved_url,
            "provenance": provenance,
        }
    ]
