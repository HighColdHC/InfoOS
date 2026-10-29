"""Validated, filter-bound pagination for opportunity evidence and breakdowns."""
import base64
import binascii
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Generic, TypeVar, TypedDict, cast

from backend.read_models.opportunity_board_data import follow_up_summary
from backend.domain.json_types import JsonObject

from backend.domain.intelligence.opportunity import (
    ENUM_MEANINGS, OPPORTUNITY_CANDIDATE_RULES_VERSION, OPPORTUNITY_LENS_VERSION,
)
from backend.infrastructure.storage.card_entry import project_display_titles
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.opportunity_candidates import OpportunityCandidateRepository, OpportunityCandidateRow
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository, OpportunityListRow


PageRow = TypeVar("PageRow", bound=Mapping[str, object])


class OpportunityPage(TypedDict, Generic[PageRow]):
    items: list[PageRow]
    next_cursor: str | None


class OpportunityCandidatePage(TypedDict):
    items: list[OpportunityCandidateRow]
    next_cursor: str | None
    profile_version: int | None


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


def list_opportunity_candidates(base_dir: Path, since: str, until: str | None = None, limit: int = 50,
                                cursor: str | None = None, only_unbroken: bool = True) -> OpportunityCandidatePage:
    _limit(limit)
    if type(only_unbroken) is not bool:
        raise ValueError("invalid_only_unbroken")
    if until is not None and _stamp(since) > _stamp(until):
        raise ValueError("invalid_window")
    start, end = _stamp(since, entry=True), _stamp(until, entry=True) if until is not None else None
    profile = InterestProfileRepository(base_dir).get()
    profile_version = profile["profile_version"] if profile else None
    context = _context(["candidates", start, end, only_unbroken, OPPORTUNITY_LENS_VERSION,
                        OPPORTUNITY_CANDIDATE_RULES_VERSION, profile_version])
    rows = OpportunityCandidateRepository(base_dir).page(since=start, until=end, limit=limit,
        after=_cursor(cursor, context), only_unbroken=only_unbroken, profile_version=profile_version)
    return {**_page(base_dir, rows, limit, context, "first_seen_at", "card_id"), "profile_version": profile_version}


def list_opportunities(base_dir: Path, since: str | None = None, next_step: str | None = None,
                       min_replicability: int | None = None, limit: int = 20,
                       cursor: str | None = None) -> OpportunityPage[OpportunityListRow]:
    _limit(limit)
    if next_step is not None and (not isinstance(next_step, str) or next_step not in ENUM_MEANINGS["next_step"]):
        raise ValueError("invalid_next_step")
    if min_replicability is not None and (type(min_replicability) is not int or not 0 <= min_replicability <= 100):
        raise ValueError("invalid_min_replicability")
    start = _stamp(since) if since is not None else None
    context = _context(["opportunities", start, next_step, min_replicability])
    rows = OpportunityBreakdownRepository(base_dir).list_latest(since=start, next_step=next_step,
        min_replicability=min_replicability, after=_cursor(cursor, context), limit=limit)
    page = _page(base_dir, rows, limit, context, "created_at", "breakdown_id")
    follow_ups = follow_up_summary(base_dir, page["items"])
    for row in page["items"]:
        row["follow_up"] = follow_ups[row["card_id"]]
    return page
