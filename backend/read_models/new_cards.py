"""New card paging with opaque, filter-bound cursors and reader links."""
from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path
from typing import NotRequired, TypedDict, cast
from urllib.parse import quote

from backend.domain.advertising import AdMetadata, advertising_projection
from backend.domain.intelligence.rubric import RUBRIC_VERSION
from backend.domain.intelligence.knowledge import freshness
from backend.domain.timestamps import utc_now_micro_z
from backend.domain.timestamps import normalize_timestamp
from backend.infrastructure.storage.entry_timestamp import entry_key
from backend.infrastructure.runtime.environment import public_base_url
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.card_entry import project_display_titles
from backend.infrastructure.storage.new_cards import NewCardDeepLink, read_new_cards


class NewCardItem(TypedDict):
    card_id: str
    title: str
    platform: str
    author: str | None
    published_at: str | None
    first_seen_at: str
    has_evaluation: bool
    freshness: str
    is_digest: bool | None
    excerpt: str
    deep_link: NewCardDeepLink
    ad: NotRequired[AdMetadata]


class NewCardPage(TypedDict):
    items: list[NewCardItem]
    next_cursor: str | None


def _stamp(value: str) -> str:
    from datetime import datetime
    try:
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError()
        stamp = normalize_timestamp(value)
        if stamp is None:
            raise ValueError()
        return entry_key(stamp)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("since/until must be ISO timestamps with timezone") from None


def list_new_cards(base_dir: Path, since: str, until: str | None = None, limit: int = 50,
                   cursor: str | None = None, only_unevaluated: bool = True, platform: str | None = None) -> NewCardPage:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    start, end = _stamp(since), _stamp(until) if until is not None else None
    if end is not None and start > end:
        raise ValueError("since must not be later than until")
    profile = InterestProfileRepository(base_dir).get()
    context = sha256(json.dumps([start, end, only_unevaluated, platform, RUBRIC_VERSION,
                               profile["profile_version"] if profile else None]).encode()).hexdigest()
    after = None
    if cursor:
        try:
            data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if data["context"] != context or not isinstance(data["id"], str):
                raise ValueError()
            after = (_stamp(data["time"]), data["id"])
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ValueError("invalid_cursor") from None
    rows = read_new_cards(base_dir, since=start, until=end, after=after, limit=limit,
                          only_unevaluated=only_unevaluated, platform=platform, rubric_version=RUBRIC_VERSION)
    items, next_cursor = rows[:limit], None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = base64.urlsafe_b64encode(json.dumps({"context": context, "time": last["first_seen_at"],
                                                         "id": last["card_id"]}).encode()).decode()
    project_display_titles(base_dir, items, excerpt=True)
    origin = public_base_url()
    now = utc_now_micro_z()
    for item in items:
        item["freshness"] = freshness(item["published_at"], item["first_seen_at"], now)
        item.update(advertising_projection(item.pop("raw_payload", None)))
        item["has_evaluation"] = bool(item["has_evaluation"])
        item["deep_link"] = {"url": (origin or "") + "/r/card/" + quote(item["card_id"], safe=""),
                             "relative_url": origin is None}
    return {"items": cast(list[NewCardItem], items), "next_cursor": next_cursor}
