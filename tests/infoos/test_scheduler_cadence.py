import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.domain.schedule.cadence import cadence_to_seconds, compute_next_run_at, is_due


NOW = datetime(2026, 4, 30, 8, 0, tzinfo=timezone.utc)


def test_cadence_to_seconds_accepts_named_values():
    assert cadence_to_seconds("hourly") == 3600
    assert cadence_to_seconds("every_6_hours") == 21600
    assert cadence_to_seconds("daily") == 86400
    assert cadence_to_seconds("weekly") == 604800


def test_cadence_to_seconds_accepts_explicit_interval():
    assert cadence_to_seconds("every_15_minutes") == 900
    assert cadence_to_seconds("every_2_days") == 172800


def test_cadence_to_seconds_rejects_unknown_value():
    with pytest.raises(ValueError, match="unsupported cadence"):
        cadence_to_seconds("whenever")


def test_is_due_when_no_previous_run():
    assert is_due(cadence="daily", now=NOW, last_finished_at=None)


def test_is_due_when_elapsed_interval_reached():
    assert is_due(cadence="daily", now=NOW, last_finished_at="2026-04-29T07:59:00Z")


def test_is_not_due_when_interval_not_reached():
    assert not is_due(cadence="hourly", now=NOW, last_finished_at="2026-04-30T07:30:00Z")


def test_compute_next_run_at_uses_last_finished_at():
    assert compute_next_run_at(cadence="daily", last_finished_at="2026-04-29T08:00:00Z") == "2026-04-30T08:00:00Z"
