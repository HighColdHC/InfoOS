"""Derive board stages from append-only user decisions and verification facts."""
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from backend.domain.timestamps import parse_utc

PLAN_STALE_DAYS = 3
CONTACT_STALE_DAYS = 14
STAGES = ("inbox", "plan", "contact", "action", "archive")
FOLLOW_UP_LABELS = {"following": "你决定跟进", "dismissed": "你选择暂不跟进", "reset": "恢复自动阶段"}


class FollowUpFact(TypedDict):
    state: str
    created_at: str


class VerificationFact(TypedDict):
    conclusion: str
    created_at: str


class Stage(TypedDict):
    stage: str | None
    stage_since: str | None
    stale: bool
    archive_reason: str | None


def derive_stage(next_step: str, created_at: str, follow_up: FollowUpFact | None,
                 verification: VerificationFact | None, include_observe: bool = False,
                 *, now: datetime | None = None) -> Stage:
    stage, since, archive = None, None, None
    if follow_up is not None and (verification is None or
            parse_utc(follow_up["created_at"]) > parse_utc(verification["created_at"])):
        if follow_up["state"] != "reset":
            stage = "plan" if follow_up["state"] == "following" else "archive"
            since = follow_up["created_at"]
            archive = "dismissed" if stage == "archive" else None
    if stage is None and verification is not None:
        stage = {"keep_observing": "contact", "to_action": "action", "drop": "archive"}[verification["conclusion"]]
        since = verification["created_at"]
        archive = "dropped" if stage == "archive" else None
    if stage is None and (next_step == "verify" or (include_observe and next_step == "observe")):
        stage, since = "inbox", created_at
    stale = False
    if stage in {"plan", "contact"}:
        threshold = PLAN_STALE_DAYS if stage == "plan" else CONTACT_STALE_DAYS
        stale = (datetime.now(timezone.utc) if now is None else now) - parse_utc(since) > timedelta(days=threshold)
    return {"stage": stage, "stage_since": since, "stale": stale, "archive_reason": archive}
