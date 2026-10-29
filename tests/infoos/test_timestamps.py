"""Contracts for the timestamp spellings already used by backend callers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import re
import time

import pytest

from backend.domain import timestamps
from backend.domain.schedule.cadence import format_utc as cadence_format_utc
from backend.domain.task_queue_policy import queue_policy_for
from backend.domain.tasks import TaskType


SECOND_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
MICRO_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
OFFSET = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+00:00\Z")


def test_existing_clock_formats_and_precision(monkeypatch):
    instant = datetime(2026, 9, 28, 12, 34, 56, 123456, tzinfo=timezone.utc)
    monkeypatch.setattr(timestamps, "utc_now", lambda: instant)
    assert timestamps.utc_now_seconds_z() == "2026-09-28T12:34:56Z"
    assert timestamps.utc_now_micro_z() == "2026-09-28T12:34:56.123456Z"
    assert timestamps.utc_now_offset() == "2026-09-28T12:34:56.123456+00:00"
    assert timestamps.utc_now_display_minutes() == "2026-09-28 12:34 UTC"
    assert SECOND_Z.fullmatch(timestamps.utc_now_seconds_z())
    assert MICRO_Z.fullmatch(timestamps.utc_now_micro_z())
    assert OFFSET.fullmatch(timestamps.utc_now_offset())
    assert timestamps.parse_utc(timestamps.utc_now_micro_z()) == instant
    assert timestamps.parse_utc(timestamps.utc_now_seconds_z()) == instant.replace(microsecond=0)
    assert timestamps.parse_utc(timestamps.utc_now_offset()) == instant


def test_utc_conversion_and_existing_parse_policies():
    local = datetime(2026, 9, 28, 20, 34, 56, 123456, tzinfo=timezone(timedelta(hours=8)))
    assert timestamps.parse_utc(local.isoformat()) == datetime(2026, 9, 28, 12, 34, 56, 123456, tzinfo=timezone.utc)
    assert timestamps.parse_utc("2026-09-28T12:34:56") == local.astimezone(timezone.utc).replace(microsecond=0)
    assert timestamps.format_utc_seconds_z(local) == "2026-09-28T12:34:56Z"
    assert timestamps.format_utc_z(local) == "2026-09-28T12:34:56.123456Z"
    assert timestamps.parse_utc_or_none("bad") is None
    assert timestamps.parse_utc_or_none(None) is None
    assert timestamps.parse_iso_or_none("2026-09-28T12:34:56").tzinfo is None
    assert timestamps.parse_iso_or_none("bad") is None
    assert timestamps.coerce_datetime(local) is local


@pytest.mark.parametrize("process_tz", ["Asia/Shanghai", "UTC"])
def test_naive_and_aware_contracts_in_process_timezone(process_tz, monkeypatch):
    previous_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = process_tz
        time.tzset()
        naive = datetime(2026, 1, 15, 12, 34, 56, 123456)
        aware = naive.replace(tzinfo=timezone(timedelta(hours=8)))
        utc_naive = naive.replace(tzinfo=timezone.utc)
        aware_utc = aware.astimezone(timezone.utc)
        monkeypatch.setattr(timestamps, "utc_now", lambda: utc_naive)

        # Explicit API/parser policy: naive input is UTC, independently of TZ.
        assert timestamps.as_utc_datetime(naive) == utc_naive
        assert timestamps.as_utc_datetime(aware) == aware_utc
        assert timestamps.parse_utc(naive.isoformat()) == utc_naive
        assert timestamps.parse_utc(aware.isoformat()) == aware_utc
        assert timestamps.parse_utc_or_none(naive.isoformat()) == utc_naive
        assert timestamps.parse_utc_or_none(aware.isoformat()) == aware_utc
        assert timestamps.parse_utc_or_now(naive.isoformat()) == utc_naive
        assert timestamps.parse_utc_or_now(aware.isoformat()) == aware_utc
        assert timestamps.parse_utc_or_now(None) == utc_naive
        assert timestamps.parse_iso_or_none(naive.isoformat()) == naive
        assert timestamps.parse_iso_or_none(aware.isoformat()) == aware
        assert timestamps.coerce_datetime(naive) is naive
        assert timestamps.coerce_datetime(aware) is aware
        assert timestamps.coerce_datetime(naive.isoformat()) == utc_naive
        assert timestamps.coerce_datetime(aware.isoformat()) == aware_utc

        assert timestamps.format_utc_seconds_z(naive) == "2026-01-15T12:34:56Z"
        assert timestamps.format_utc_z(naive) == "2026-01-15T12:34:56.123456Z"
        assert timestamps.format_utc_seconds_z(aware) == "2026-01-15T04:34:56Z"
        assert timestamps.format_utc_z(aware) == "2026-01-15T04:34:56.123456Z"

        # Old callsite chains normalized string/API input before formatting.
        legacy_api = utc_naive.isoformat(timespec="seconds").replace("+00:00", "Z")
        assert timestamps.format_utc_seconds_z(timestamps.as_utc_datetime(naive)) == legacy_api
        legacy_queue = timestamps.parse_utc(naive.isoformat()).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        assert timestamps.format_utc_seconds_z(timestamps.parse_utc(naive.isoformat())) == legacy_queue
        deadline = queue_policy_for(TaskType.FETCH, platform="rss", enqueued_at=naive.isoformat(), next_run_at=None).deadline_at
        assert deadline == "2026-01-15T13:34:56Z"

        # Cadence's public datetime formatter historically interprets naive as local.
        old_cadence_naive = naive.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        old_cadence_aware = aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        assert old_cadence_naive == (
            "2026-01-15T04:34:56.123456Z" if process_tz == "Asia/Shanghai"
            else "2026-01-15T12:34:56.123456Z"
        )
        assert cadence_format_utc(naive) == old_cadence_naive
        assert cadence_format_utc(aware) == old_cadence_aware
        assert timestamps.format_local_naive_utc_z(naive) == old_cadence_naive
    finally:
        if previous_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous_tz
        time.tzset()
