"""Daily brief profile deferral through the real processor persistence path."""
from datetime import datetime, timezone

import pytest

from backend.application.tasks import registry
from backend.application.tasks.daily_brief_planner import enqueue_daily_brief_build_task
from backend.application.tasks.processor_execution import process_task
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.usecases.daily_brief import brief_window
from backend.application.usecases.interest_profiles import push_profile
from backend.domain.intelligence.daily_brief import profile_retry_at
from tests.infoos.test_daily_intelligence import PROFILE, _db


def _clock(monkeypatch, timestamp):
    instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or timezone.utc)

    monkeypatch.setattr(registry, "datetime", Clock)


@pytest.mark.parametrize("now,expected", [
    ("2026-09-30T23:00:00Z", "2026-09-30T23:30:00Z"),
    ("2026-10-01T15:59:00Z", "2026-10-01T16:29:00Z"),
    ("2026-10-01T15:59:00.000001Z", None),
    ("2026-10-01T16:00:00Z", None),
    ("2026-10-02T01:00:00Z", None),
])
def test_missing_profile_retry_deadline(tmp_path, monkeypatch, now, expected):
    _db(tmp_path)
    _clock(monkeypatch, now)
    task = Task(task_id="brief", task_type=TaskType.DAILY_BRIEF_BUILD,
                target_kind="daily_brief", target_id="2026-10-01")
    result = registry._handle_daily_brief(task, tmp_path)
    assert result["artifacts"]["reason"] == "profile_missing"
    assert result["status"] == ("pending" if expected else "skipped")
    assert result.get("next_run_at") == expected
    assert profile_retry_at(task.target_id, datetime.fromisoformat(now.replace("Z", "+00:00"))) == expected


def test_defer_preserves_attempt_and_profile_arrival_keeps_original_window(tmp_path, monkeypatch):
    _db(tmp_path)
    now = "2026-09-30T23:00:00Z"
    _clock(monkeypatch, now)
    task_id = enqueue_daily_brief_build_task(tmp_path, now=now)
    repo = TaskRepository.for_base_dir(tmp_path)
    claimed = repo.claim_task_ids([task_id], worker_id="worker", now=now,
                                 lease_expires_at="2026-10-01T10:00:00Z")[0]
    attempt = claimed.attempt
    result = process_task(claimed, tmp_path, now=now, worker_id="worker", lease_seconds=60)
    assert result["status"] == "pending"
    pending = repo.get(task_id)
    assert pending.status == TaskStatus.PENDING
    assert pending.attempt == attempt
    assert pending.next_run_at == "2026-09-30T23:30:00Z"
    assert pending.error_reason == "profile_missing"
    assert pending.claimed_by is None
    push_profile(tmp_path, PROFILE, source="retry-test")
    later = "2026-10-01T03:00:00Z"
    _clock(monkeypatch, later)
    reclaimed = repo.claim_task_ids([task_id], worker_id="worker", now=later,
                                   lease_expires_at="2026-10-01T10:00:00Z")[0]
    completed = process_task(reclaimed, tmp_path, now=later, worker_id="worker", lease_seconds=60)
    assert completed["status"] == "success"
    brief = completed["artifacts"]["brief"]
    assert brief["brief_date"] == "2026-10-01"
    assert (brief["window_start"], brief["window_end"]) == brief_window("2026-10-01")
    assert repo.get(task_id).attempt == attempt


def test_processor_skips_missing_profile_after_deadline(tmp_path, monkeypatch):
    _db(tmp_path)
    task_id = enqueue_daily_brief_build_task(tmp_path, now="2026-09-30T23:00:00Z")
    now = "2026-10-01T16:00:00Z"
    _clock(monkeypatch, now)
    repo = TaskRepository.for_base_dir(tmp_path)
    claimed = repo.claim_task_ids([task_id], worker_id="worker", now=now,
                                 lease_expires_at="2026-10-01T17:00:00Z")[0]
    result = process_task(claimed, tmp_path, now=now, worker_id="worker", lease_seconds=60)
    assert result["status"] == "skipped"
    assert repo.get(task_id).artifacts_summary["reason"] == "profile_missing"
    assert repo.get(task_id).attempt == claimed.attempt
