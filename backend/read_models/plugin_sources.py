"""Read-only source catalog projection for plugin clients."""
from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as BinasciiError
import json
from pathlib import Path
import re
from typing import TypedDict

from backend.infrastructure.storage.sources import SourceRepository, SourceRow
from backend.infrastructure.storage.plugin_card_queries import plugin_card_queries


class PluginSourceItem(TypedDict):
    source_id: str
    display_name: str
    platform: str
    source_type: str
    card_count: int
    latest_card_updated_at: str | None


class PluginSourcePage(TypedDict):
    schema: str
    items: list[PluginSourceItem]
    next_page_token: str | None


SOURCE_SCHEMA = "infoos.source-catalog.v1"
_CURSOR_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class InvalidSourcePageTokenError(ValueError):
    def __init__(self):
        super().__init__("invalid source page token")


def read_plugin_source_page(
    base_dir: Path | str,
    *,
    page_size: int = 100,
    page_token: str | None = None,
) -> PluginSourcePage:
    base_path = Path(base_dir)
    cursor = _decode_cursor(page_token) if page_token else None
    sources = [
        source
        for source in SourceRepository(base_path).list_sources(include_disabled=False)
        if not source["raw_metadata"].get("system_managed")
    ]
    sources.sort(key=lambda source: source["source_id"])
    if cursor is not None:
        sources = [source for source in sources if source["source_id"] > cursor]

    card_stats = _stable_card_stats(base_path)
    bounded_size = max(1, min(int(page_size), 200))
    selected = sources[: bounded_size + 1]
    has_more = len(selected) > bounded_size
    page = selected[:bounded_size]
    next_page_token = (
        _encode_cursor(page[-1]["source_id"])
        if has_more and page
        else None
    )
    return {
        "schema": SOURCE_SCHEMA,
        "items": [_source_item(source, card_stats) for source in page],
        "next_page_token": next_page_token,
    }


def _stable_card_stats(base_path: Path) -> dict[str, tuple[int, str | None]]:
    with plugin_card_queries(base_path) as reader:
        return reader.stable_card_stats()


def _source_item(
    source: SourceRow,
    card_stats: dict[str, tuple[int, str | None]],
) -> PluginSourceItem:
    source_id = source["source_id"]
    card_count, latest_card_updated_at = card_stats.get(source_id, (0, None))
    return {
        "source_id": source_id,
        "display_name": source["display_name"] or source_id,
        "platform": source["platform"],
        "source_type": source["source_type"],
        "card_count": card_count,
        "latest_card_updated_at": latest_card_updated_at,
    }


def _encode_cursor(source_id: str) -> str:
    payload = json.dumps(
        {"source_id": source_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: str) -> str:
    try:
        if not value or not _CURSOR_PATTERN.fullmatch(value):
            raise ValueError
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(urlsafe_b64decode(value + padding).decode("utf-8"))
        if set(decoded) != {"source_id"} or not isinstance(decoded["source_id"], str):
            raise ValueError
        if not decoded["source_id"]:
            raise ValueError
        return decoded["source_id"]
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, BinasciiError) as exc:
        raise InvalidSourcePageTokenError() from exc
