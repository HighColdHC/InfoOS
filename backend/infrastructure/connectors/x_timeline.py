"""Compare X newest-first timeline cursors by numeric tweet identity."""
from __future__ import annotations

from backend.domain.sources.acquisition import normalize_acquisition
from backend.infrastructure.connectors.opencli import OpenCLIConnector


class XTimelineConnector(OpenCLIConnector):
    def _fetch_new_items_in_lease(self, since_cursor: str | None):
        if self.source_platform != "x" or self.result_order != "newest_first" or not _numeric(since_cursor):
            return super()._fetch_new_items_in_lease(since_cursor)

        # Load the visible batch once through the shared normalization/evidence path.
        result = super()._fetch_new_items_in_lease(None)
        cursor = int(since_cursor)
        numeric_ids = [int(item.raw_item_id) for item in result.items if _numeric(item.raw_item_id)]
        result.items = [item for item in result.items
                        if not _numeric(item.raw_item_id) or int(item.raw_item_id) > cursor]
        result.new_count = len(result.items)
        result.dedup_skipped_count = max(0, result.visible_count - result.new_count)
        newest = max([cursor, *numeric_ids])
        result.next_cursor = str(newest) if newest > cursor else since_cursor
        limit = _command_limit(self.command)
        if numeric_ids and min(numeric_ids) > cursor and limit and result.visible_count >= limit:
            result.acquisition = normalize_acquisition({
                "requested_count": limit,
                "observed_count": result.visible_count,
                "complete": False,
                "stop_reason": "cursor_not_reached",
                "stop_position": None,
                "stop_message": f"{min(numeric_ids)} 晚于游标 {since_cursor}，两轮之间可能有推文未抓到",
            })
        return result


def _numeric(value: str | None) -> bool:
    return isinstance(value, str) and value.isascii() and value.isdecimal()


def _command_limit(command: list[str]) -> int | None:
    for index, token in enumerate(command):
        value = token.removeprefix("--limit=") if token.startswith("--limit=") else (
            command[index + 1] if token == "--limit" and index + 1 < len(command) else None
        )
        if _numeric(value) and int(value) > 0:
            return int(value)
    return None
