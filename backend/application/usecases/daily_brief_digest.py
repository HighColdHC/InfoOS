"""Queue the digest only after a daily brief is frozen for the first time."""
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from backend.application.usecases.notifications import enqueue_notification
from backend.application.usecases.reader_links import brief_link, card_link, reader_link
from backend.domain.intelligence.brief_digest import format_brief_digest
from backend.domain.notifications import NotificationKind
from backend.domain.timestamps import format_utc_seconds_z
from backend.infrastructure.notifications.config import load_config


def enqueue_daily_brief_digest(base_dir: Path | str, brief: dict[str, Any]) -> list[str]:
    if not load_config()["content_push"]["daily_brief_digest"]:
        return []
    payload = {**brief, "items": [{**item, "deep_link": reader_link(item["deep_link"])["url"] if brief.get("layout") == "knowledge_v1" else card_link(item["card_id"])["url"]}
                                  for item in brief["items"]]}
    weekly = brief["weekly_paused"]
    if weekly is not None and weekly["top"] is not None:
        payload["weekly_paused"] = {**weekly, "top": {**weekly["top"],
            "deep_link": reader_link(weekly["top"]["deep_link"])["url"]}}
    title, body = format_brief_digest(payload, brief_link(brief["brief_date"])["url"])
    day = date.fromisoformat(brief["brief_date"])
    expiry = datetime(day.year, day.month, day.day, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    return enqueue_notification(base_dir, kind=NotificationKind.DAILY_BRIEF_DIGEST, title=title, body=body,
        dedupe_key=f"daily-brief:{day.isoformat()}", related=("brief", day.isoformat()),
        expires_at=format_utc_seconds_z(expiry.astimezone(timezone.utc)))
