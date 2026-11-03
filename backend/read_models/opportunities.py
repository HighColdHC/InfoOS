"""Validated, filter-bound pagination for opportunity evidence and breakdowns."""
import base64
import binascii
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Generic, TypeVar, TypedDict, cast

from backend.domain.json_types import JsonObject
from backend.infrastructure.storage.card_entry import project_display_titles


PageRow = TypeVar("PageRow", bound=Mapping[str, object])


class OpportunityPage(TypedDict, Generic[PageRow]):
    items: list[PageRow]
    next_cursor: str | None


def _stamp(value: str, *, entry: bool = False) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
        stamp = stamp.astimezone(timezone.utc)
        if entry and stamp.microsecond:
            stamp = stamp.replace(microsecond=0) + timedelta(seconds=1)
        return stamp.isoformat(timespec="seconds" if entry else "microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise ValueError("invalid_timestamp") from None


def _cursor(cursor: str | None, context: str) -> tuple[str, str] | None:
    if cursor is None or cursor == "":
        return None
    try:
        data: JsonObject = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(data, dict) or data["context"] != context or not isinstance(data["id"], str):
            raise ValueError()
        _stamp(cast(str, data["time"]))
        return cast(str, data["time"]), data["id"]
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError):
        raise ValueError("invalid_cursor") from None


def _page(base_dir: Path, rows: list[PageRow], limit: int, context: str,
          time_field: str, id_field: str) -> OpportunityPage[PageRow]:
    items = rows[:limit]
    cursor = None
    if len(rows) > limit:
        last = items[-1]
        cursor = base64.urlsafe_b64encode(json.dumps({"context": context, "time": last[time_field],
                                                    "id": last[id_field]}).encode()).decode()
    project_display_titles(base_dir, items)
    return {"items": items, "next_cursor": cursor}


def _context(values: object) -> str:
    return sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _limit(limit: object) -> None:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid_limit")
