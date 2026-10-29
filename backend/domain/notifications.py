"""Pure outbound notification policy."""
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo


class NotificationKind(str, Enum):
    DAILY_BRIEF_DIGEST = "daily_brief_digest"
    CHANNEL_TEST = "channel_test"
    COMMAND_REPLY = "command_reply"
    AUTH_RECOVERY_QR = "auth_recovery_qr"
    AUTH_RECOVERY_TEXT = "auth_recovery_text"
    AUTH_RECOVERED = "auth_recovered"
    AGENT_WRITEBACK_STALLED = "agent_writeback_stalled"


KINDS = {
    NotificationKind.DAILY_BRIEF_DIGEST: ("info", False),
    NotificationKind.CHANNEL_TEST: ("info", True),
    NotificationKind.COMMAND_REPLY: ("info", True),
    NotificationKind.AUTH_RECOVERY_QR: ("warning", False),
    NotificationKind.AUTH_RECOVERY_TEXT: ("warning", False),
    NotificationKind.AUTH_RECOVERED: ("info", False),
    NotificationKind.AGENT_WRITEBACK_STALLED: ("warning", False),
}


def delivery_time(now: datetime, quiet: dict, kind: NotificationKind, *, manual: bool = False) -> datetime:
    if KINDS[kind][1] or (manual and kind == NotificationKind.AUTH_RECOVERY_QR):
        return now
    local = now.astimezone(ZoneInfo(quiet["timezone"]))
    start, end = time.fromisoformat(quiet["start"]), time.fromisoformat(quiet["end"])
    clock = local.time().replace(tzinfo=None)
    silent = (start <= clock < end) if start < end else (clock >= start or clock < end)
    if start == end or not silent:
        return now
    date = local.date() + timedelta(days=int(start > end and clock >= start))
    return datetime.combine(date, end, local.tzinfo).astimezone(now.tzinfo)


# QR images and private-command replies stay on Feishu.
_FEISHU_ONLY = {NotificationKind.AUTH_RECOVERY_QR: ("feishu",), NotificationKind.COMMAND_REPLY: ("feishu",)}


def route_channels(kind: NotificationKind, readiness: dict[str, bool]) -> tuple[str, ...]:
    allowed = _FEISHU_ONLY.get(kind, tuple(readiness))
    return tuple(channel for channel, ready in readiness.items() if ready and channel in allowed)
