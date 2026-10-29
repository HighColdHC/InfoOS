from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from backend.application.enrichment.web_content import _x_tweet_id
from backend.application.enrichment.x_blocks._helpers import _upsert_block
from backend.application.workflows.browser_acquisition_batch import (
    active_browser_acquisition_batch,
    bind_browser_acquisition_batch_state,
)
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.observability.logging import get_logger
from backend.infrastructure.opencli.tab_lease import (
    bind_opencli_lease_event_sink,
    current_opencli_lease_event_sink,
)

_logger = get_logger("enrichment.x_blocks")


def enrich_x_card_blocks(card_id: str, raw_item: RawItem, base_dir: Path) -> list[dict[str, Any]]:
    """Fetch X Article and thread content for an X card and write results into
    card_content_blocks. Never modifies raw_item.raw_text.

    Returns list of created block dicts.
    """
    if raw_item.source_platform != "x":
        return []

    from backend.application.enrichment.x_blocks.article import _enrich_article
    from backend.application.enrichment.x_blocks.quote import _enrich_quote
    from backend.application.enrichment.x_blocks.reply import _enrich_reply_parent
    from backend.application.enrichment.x_blocks.repost import _enrich_repost
    from backend.application.enrichment.x_blocks.tco import _enrich_tco_links
    from backend.infrastructure.storage.repositories import CardContentBlockRepository

    repo = CardContentBlockRepository(base_dir)
    blocks: list[dict[str, Any]] = []
    source_url = raw_item.source_url
    record = _opencli_record(raw_item)
    post_kind = _x_post_kind(record, raw_item.raw_text)

    # 1. Preserve original tweet text as layer="original"
    if raw_item.raw_text and post_kind != "retweet":
        _upsert_block(
            repo,
            card_id=card_id,
            position=0,
            layer="original",
            block_kind="original_tweet",
            status="preserved",
            body=raw_item.raw_text,
            source_url=source_url,
            provenance="Source tweet",
            raw_payload={"content_role": "primary", "relation_depth": 0, "post_kind": post_kind},
        )
        blocks.append(
            {
                "block_id": f"block_{card_id}_original_tweet",
                "card_id": card_id,
                "position": 0,
                "layer": "original",
                "block_kind": "original_tweet",
                "status": "preserved",
                "body": raw_item.raw_text,
                "source_url": source_url,
                "provenance": "Source tweet",
                "raw_payload": {"content_role": "primary", "relation_depth": 0, "post_kind": post_kind},
            }
        )

    tweet_id = _x_tweet_id(source_url)
    if not tweet_id:
        return blocks

    # Run enrichment steps concurrently
    from concurrent.futures import ThreadPoolExecutor, as_completed

    event_sink = current_opencli_lease_event_sink()
    browser_batch = active_browser_acquisition_batch()

    def _run_step(func, *args: Any) -> list[dict[str, Any]]:
        try:
            # Propagate only task-event attribution. Active leases are deliberately
            # excluded because sibling workers must independently serialize at the
            # real OpenCLI gateway.
            with bind_browser_acquisition_batch_state(browser_batch):
                with bind_opencli_lease_event_sink(event_sink):
                    return func(*args)
        except (OSError, subprocess.SubprocessError):
            _logger.exception("X enrichment step failed for %s", card_id)
            return []

    steps: list[Any] = [
        (lambda: _enrich_article(card_id, tweet_id, source_url, repo)),
        (lambda: _enrich_tco_links(card_id, raw_item, repo)),
    ]
    if post_kind == "retweet":
        steps.append(lambda: _enrich_repost(card_id, tweet_id, source_url, repo, raw_item))
    elif post_kind == "quote":
        steps.append(lambda: _enrich_quote(card_id, tweet_id, source_url, repo, raw_item))
    elif post_kind == "reply":
        steps.append(lambda: _enrich_reply_parent(card_id, source_url, repo, raw_item))

    created: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(_run_step, step_fn) for step_fn in steps]
        for future in as_completed(futures):
            created.extend(future.result())

    blocks.extend(sorted(created, key=lambda b: b["position"]))

    return blocks


def _opencli_record(raw_item: RawItem) -> dict[str, Any]:
    metadata = raw_item.raw_metadata
    direct = metadata.get("opencli_record")
    if isinstance(direct, dict):
        return direct
    nested = metadata.get("raw_payload")
    if isinstance(nested, dict) and isinstance(nested.get("opencli_record"), dict):
        return nested["opencli_record"]
    return {}


def _x_post_kind(record: dict[str, Any], raw_text: str | None) -> str:
    if record.get("is_retweet") is True or any(
        isinstance(record.get(key), dict) for key in ("retweeted_status", "retweeted_tweet", "retweet")
    ) or (raw_text or "").lstrip().startswith("RT @"):
        return "retweet"
    if any(isinstance(record.get(key), dict) for key in ("quoted_status", "quoted_tweet", "quote")):
        return "quote"
    if any(
        record.get(key) not in (None, "", 0, False)
        for key in ("in_reply_to_status_id", "in_reply_to_status_id_str", "in_reply_to_tweet_id")
    ) or any(isinstance(record.get(key), dict) for key in ("replied_to_tweet", "parent_tweet")):
        return "reply"
    return "original"
