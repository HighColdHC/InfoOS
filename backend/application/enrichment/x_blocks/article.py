from __future__ import annotations

from typing import Any

from backend.application.enrichment.web_content import _run_opencli_json_command
from backend.application.enrichment.x_blocks._helpers import _upsert_block


def _enrich_article(
    card_id: str,
    tweet_id: str,
    source_url: str,
    repo: Any,
) -> list[dict[str, Any]]:
    """Fetch X Article content. Returns list of block dicts."""
    article_payload = _run_opencli_json_command(
        ["opencli", "twitter", "article", tweet_id, "--format", "json"],
        timeout=15,
    )
    if not isinstance(article_payload, dict):
        return []

    article_content = str(article_payload.get("content") or "").strip()
    if not article_content:
        return []

    _upsert_block(
        repo,
        card_id=card_id,
        position=1,
        layer="context",
        block_kind="x_article",
        status="enriched",
        body=article_content,
        source_url=source_url,
        provenance="X Article fetch via opencli",
    )
    return [
        {
            "block_id": f"block_{card_id}_x_article",
            "card_id": card_id,
            "position": 1,
            "layer": "context",
            "block_kind": "x_article",
            "status": "enriched",
            "body": article_content,
            "source_url": source_url,
            "provenance": "X Article fetch via opencli",
        }
    ]
