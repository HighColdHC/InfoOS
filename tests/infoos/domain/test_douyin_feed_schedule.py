from datetime import datetime
from random import Random

import pytest

from backend.domain.sources.douyin_home_feed import first_session_at, home_feed_options, next_session_at


class FixedRandom(Random):
    def __init__(self, *values):
        self.values = iter(values)

    def randint(self, a, b):
        value = next(self.values)
        assert a <= value <= b
        return value


@pytest.mark.parametrize("finished,values,expected", [
    ("2026-10-09T10:00:00+08:00", (90,), "2026-10-09T11:30:00+08:00"),
    ("2026-10-09T18:30:00+08:00", (60, 15), "2026-10-10T09:15:00+08:00"),
    ("2026-10-09T23:30:00+08:00", (120, 60), "2026-10-10T10:00:00+08:00"),
    ("2026-10-10T00:15:00+08:00", (60, 0), "2026-10-10T09:00:00+08:00"),
    ("2026-10-09T18:00:00+08:00", (60, 0), "2026-10-10T09:00:00+08:00"),
])
def test_next_session_is_based_on_completion_and_stays_in_window(finished, values, expected):
    result = next_session_at(datetime.fromisoformat(finished), None, rng=FixedRandom(*values))
    assert result.isoformat() == expected


@pytest.mark.parametrize("config", [
    {"active_hours": {"start": "9:00", "end": "19:00"}},
    {"active_hours": {"start": "09:00", "end": "24:00"}},
    {"active_hours": {"start": "09:00", "end": "09:00"}},
    {"active_hours": []}, {"session_interval_min_minutes": True},
    {"session_interval_max_minutes": 90.5}, {"dwell_min_seconds": None},
])
def test_invalid_configuration(config):
    with pytest.raises(ValueError):
        home_feed_options(config)


def test_first_periodic_run_and_utc_input():
    assert first_session_at(datetime.fromisoformat("2026-10-09T00:00:00+00:00")).isoformat() == "2026-10-09T09:00:00+08:00"
    assert first_session_at(datetime.fromisoformat("2026-10-09T01:00:00+00:00")).isoformat() == "2026-10-09T09:00:00+08:00"
    with pytest.raises(ValueError):
        first_session_at(datetime(2026, 10, 9))


def test_short_configured_window_caps_opening_jitter():
    result = next_session_at(datetime.fromisoformat("2026-10-09T18:30:00+08:00"),
                             {"active_hours": {"start": "09:00", "end": "09:01"}}, rng=FixedRandom(60, 0))
    assert result.isoformat() == "2026-10-10T09:00:00+08:00"
