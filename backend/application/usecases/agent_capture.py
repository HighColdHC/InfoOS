"""Reusable deduplication and link resolution for agent-submitted captures."""
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urlsplit

from backend.domain.captures import canonical_capture_url, capture_identity_hash
from backend.domain.sources.registry import capture_platform_for_host
from backend.infrastructure.capture_link_resolution import resolve_capture_link
from backend.infrastructure.storage.capture_lookup import (
    PENDING_CAPTURE, capture_card_summary, find_capture_card,
)
from backend.infrastructure.storage.database import connect_existing
from backend.infrastructure.storage.paths import resolve_sqlite_path


def prepare_agent_capture(url: str) -> dict[str, str]:
    return resolve_capture_link(url)


def find_existing_capture(base_dir: Path | str, url: str, *,
                          connection: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """Read-only; also used inside submission's transaction to close the race."""
    if connection is None:
        with connect_existing(resolve_sqlite_path(base_dir)) as conn:
            return find_existing_capture(base_dir, url, connection=conn)
    canonical = canonical_capture_url(url)
    platform = capture_platform_for_host(urlsplit(canonical).hostname or "")
    card = find_capture_card(connection, platform, canonical)
    if card and card["status"] == "active":
        return {"status": "already_collected", **capture_card_summary(connection, card)}
    pending = connection.execute(PENDING_CAPTURE, (capture_identity_hash(platform, canonical, extended=True),)).fetchone()
    if pending:
        return {"status": "already_in_progress", **dict(pending)}
    if card:
        return {"status": "already_in_progress", **capture_card_summary(connection, card)}
    return None
