"""Notification settings request validation at the HTTP boundary."""
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class PatchModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FeishuPatch(PatchModel):
    enabled: StrictBool | None = None
    receive_id: str | None = Field(default=None, pattern=r'^ou_[A-Za-z0-9_-]+$')


class ChannelsPatch(PatchModel):
    feishu: FeishuPatch | None = None


class QuietPatch(PatchModel):
    start: str | None = Field(default=None, pattern=r'^\d{2}:\d{2}$')
    end: str | None = Field(default=None, pattern=r'^\d{2}:\d{2}$')
    timezone: str | None = None

    @field_validator('start', 'end')
    @classmethod
    def valid_time(cls, value):
        if value is not None:
            time.fromisoformat(value)
        return value

    @field_validator('timezone')
    @classmethod
    def valid_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('invalid_timezone') from None
        return value


class QrPatch(PatchModel):
    codes_per_round: int | None = Field(default=None, ge=1, le=10, strict=True)
    round_interval_hours: int | None = Field(default=None, ge=1, le=48, strict=True)
    max_rounds_per_day: int | None = Field(default=None, ge=1, le=24, strict=True)


class WatchdogPatch(PatchModel):
    evaluate_stall_hours: int | None = Field(default=None, ge=1, le=720, strict=True)
    opportunities_stall_hours: int | None = Field(default=None, ge=1, le=720, strict=True)
    evaluate_since: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
    opportunities_since: str | None = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


class EmailPatch(PatchModel):
    smtp_host: str | None = Field(default=None, min_length=1, max_length=254)
    smtp_port: int | None = Field(default=None, ge=1, le=65535, strict=True)
    username: str | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, max_length=1000, repr=False)
    from_email: str | None = Field(default=None, max_length=254)
    to_email: str | None = Field(default=None, max_length=254)


class ContentPushPatch(PatchModel):
    daily_brief_digest: StrictBool | None = None


class NotificationPatch(PatchModel):
    content_push: ContentPushPatch | None = None
    enabled: StrictBool | None = None
    email: EmailPatch | None = None
    channels: ChannelsPatch | None = None
    quiet_hours: QuietPatch | None = None
    auth_qr: QrPatch | None = None
    agent_watchdog: WatchdogPatch | None = None
