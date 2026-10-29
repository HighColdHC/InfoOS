"""Parse the card catalog's inclusive publication bounds at the HTTP boundary."""
from datetime import date, datetime, time
import re
from zoneinfo import ZoneInfo


def publication_bounds(since: str | None, until: str | None) -> tuple[datetime | None, datetime | None]:
    def parse(value: str | None) -> datetime | None:
        if value is None:
            return None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(date.fromisoformat(value), time(), ZoneInfo("Asia/Shanghai"))
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("发布时间须为 ISO 8601 日期或带时区时间；日期按北京时间 00:00 解释")
        return parsed

    start, end = parse(since), parse(until)
    if start is not None and end is not None and start > end:
        raise ValueError("published_since 不得晚于 published_until")
    return start, end
