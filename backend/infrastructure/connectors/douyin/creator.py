"""Normalize OpenCLI Douyin creator records into canonical video records."""
from __future__ import annotations

from dataclasses import replace
import json
import re
from typing import Any

from backend.infrastructure.connectors.opencli import OpenCLIConnector

INCREMENTAL_MAX_PAGES = 25
CURSOR_HISTORY_SIZE = 5
PINNED_HISTORY_SIZE = 3
CURSOR_PREFIX = "douyin-creator-v1:"
_VIDEO_URL = re.compile(r"https://www\.douyin\.com/video/(\d+)")


class DouyinCreatorConnector(OpenCLIConnector):
    """Bound output while scanning incrementals to InfoOS's durable cursor."""

    def _fetch_new_items_in_lease(self, since_cursor: str | None):
        stop_aweme_ids = _cursor_aweme_ids(since_cursor)
        command = list(self.command)
        output_limit = _positive_option(command, "--limit")
        if stop_aweme_ids:
            command = _replace_option(command, "--max_pages", str(INCREMENTAL_MAX_PAGES))
            command.extend(["--stop_aweme_id", ",".join(stop_aweme_ids)])
        effective = replace(self, command=command)
        # The shared connector's single-ID cursor comparison does not apply to
        # this creator-only history format. Only known non-pinned IDs stop it.
        result = OpenCLIConnector._fetch_new_items_in_lease(effective, None)
        reinitialized = bool(getattr(effective, "cursor_reinitialized", False))
        pinned_items = [item for item in result.items if _is_pinned(item)]
        regular_items = [item for item in result.items if not _is_pinned(item)]
        pinned_ids = _item_aweme_ids(pinned_items)[:PINNED_HISTORY_SIZE]
        observed_pinned_ids = set(_item_aweme_ids(pinned_items))
        if stop_aweme_ids and not reinitialized:
            stop_urls = {_video_url(aweme_id) for aweme_id in stop_aweme_ids}
            stop_index = next(
                (index for index, item in enumerate(regular_items) if item.raw_item_id in stop_urls),
                None,
            )
            if stop_index is not None:
                regular_items = regular_items[:stop_index]
            pinned_items = [item for item in pinned_items if item.raw_item_id not in stop_urls]
        deferred_count = 0
        if stop_aweme_ids and not reinitialized and output_limit and len(regular_items) > output_limit:
            deferred_count = len(regular_items) - output_limit
            # The adapter returns newest first. Commit the oldest contiguous
            # segment after the old cursor, then advance only to that segment's
            # newest item. Later runs can continue without skipping the gap.
            regular_items = regular_items[-output_limit:]
        result.items = [*pinned_items, *regular_items]
        result.new_count = len(result.items)
        result.dedup_skipped_count = max(0, result.visible_count - result.new_count)
        regular_ids = _item_aweme_ids(regular_items)
        if not reinitialized:
            old_regular_ids = [id for id in stop_aweme_ids if id not in observed_pinned_ids]
            regular_ids = _unique_ids([*regular_ids, *old_regular_ids])
        cursor_ids = [*regular_ids[:CURSOR_HISTORY_SIZE], *pinned_ids]
        result.next_cursor = _encode_cursor(cursor_ids) if cursor_ids else since_cursor
        self.cursor_reinitialized = reinitialized
        self.discovery_evidence = {
            **effective.discovery_evidence,
            "executor": "opencli_douyin_full_creator",
            "pagination": "bounded_initial" if since_cursor is None else "until_cursor",
            "output_limit": output_limit,
            "deferred_new_item_count": deferred_count,
            "cursor_reinitialized": reinitialized,
            "old_cursor_id_count": len(stop_aweme_ids),
            "pinned_item_count": len(observed_pinned_ids),
        }
        return result

    def _extract_records(self, payload: Any) -> list[dict[str, Any]]:
        records = super()._extract_records(payload)
        self.cursor_reinitialized = any(record.get("cursor_reinitialized") is True for record in records)
        return [
            normalized
            for record in records
            if (normalized := normalize_creator_record(record)) is not None
        ]


def _video_url(aweme_id: str) -> str:
    return f"https://www.douyin.com/video/{aweme_id}"


def _cursor_aweme_ids(cursor: str | None) -> list[str]:
    value = str(cursor or "").strip()
    if value.startswith(CURSOR_PREFIX):
        try:
            loaded = json.loads(value.removeprefix(CURSOR_PREFIX))
        except json.JSONDecodeError:
            return []
        if not isinstance(loaded, list):
            return []
        return _unique_ids([item for item in loaded if isinstance(item, str) and item.isdigit()])[
            :CURSOR_HISTORY_SIZE + PINNED_HISTORY_SIZE
        ]
    match = _VIDEO_URL.fullmatch(value)
    return [match.group(1)] if match else []


def _item_aweme_ids(items: list[Any]) -> list[str]:
    ids = []
    for item in items:
        match = _VIDEO_URL.fullmatch(item.raw_item_id)
        if match:
            ids.append(match.group(1))
    return _unique_ids(ids)


def _is_pinned(item: Any) -> bool:
    return item.raw_metadata.get("opencli_record", {}).get("is_top") is True


def _unique_ids(ids: list[str]) -> list[str]:
    return list(dict.fromkeys(ids))


def _encode_cursor(ids: list[str]) -> str:
    return CURSOR_PREFIX + json.dumps(
        ids[:CURSOR_HISTORY_SIZE + PINNED_HISTORY_SIZE], separators=(",", ":")
    )


def _replace_option(command: list[str], option: str, value: str) -> list[str]:
    updated = list(command)
    try:
        option_index = updated.index(option)
    except ValueError:
        updated.extend([option, value])
    else:
        value_index = option_index + 1
        if value_index < len(updated):
            updated[value_index] = value
        else:
            updated.append(value)
    return updated


def _positive_option(command: list[str], option: str) -> int | None:
    try:
        value = int(command[command.index(option) + 1])
    except (ValueError, IndexError):
        return None
    return value if value > 0 else None


def normalize_creator_record(record: dict[str, Any]) -> dict[str, Any] | None:
    aweme_id = str(record.get("aweme_id") or "").strip()
    if not aweme_id.isdigit():
        return None
    canonical_url = f"https://www.douyin.com/video/{aweme_id}"
    return {
        "aweme_id": aweme_id,
        "is_top": record.get("is_top") is True,
        "title": record.get("title"),
        "duration": record.get("duration"),
        "digg_count": record.get("digg_count"),
        "comment_count": record.get("comment_count"),
        "share_count": record.get("share_count"),
        "play_url": record.get("play_url"),
        "cover_url": record.get("cover_url"),
        "url": canonical_url,
        "author": record.get("author"),
        "author_handle": record.get("author_handle"),
        "author_url": record.get("author_url"),
        "author_signature": record.get("author_signature"),
        "follower_count": record.get("follower_count"),
        "following_count": record.get("following_count"),
        "author_aweme_count": record.get("author_aweme_count"),
        "total_favorited": record.get("total_favorited"),
        "top_comments": record.get("top_comments"),
        "published_at": record.get("published_at"),
    }
