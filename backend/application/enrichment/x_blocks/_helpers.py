from __future__ import annotations

from typing import Any

from urllib.parse import urlparse


def _is_x_url(url: str) -> bool:
    """Check if a URL is an X/Twitter URL."""
    hostname = urlparse(url).netloc.lower()
    return hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}


def _upsert_block(
    repo: Any,
    *,
    card_id: str,
    position: int,
    layer: str,
    block_kind: str,
    status: str,
    body: str,
    source_url: str,
    provenance: str,
    raw_payload: dict[str, Any] | None = None,
) -> None:
    repo.upsert_block(
        {
            "block_id": f"block_{card_id}_{block_kind}",
            "card_id": card_id,
            "position": position,
            "layer": layer,
            "block_kind": block_kind,
            "status": status,
            "body": body,
            "segments": [],
            "source_url": source_url,
            "provenance": provenance,
            "raw_payload": raw_payload or {},
        }
    )
