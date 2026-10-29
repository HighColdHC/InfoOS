from __future__ import annotations

from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.application.workflows.acquisition_failures import AcquisitionFailure, AcquisitionFailureError
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.storage.repositories import TaskEventRepository

from tests.infoos.faults.helpers import fetch_task, seed_source


pytestmark = pytest.mark.fault


def test_browser_disconnect_repairs_once_preserves_diagnostic_and_cleans_up(tmp_path: Path, monkeypatch) -> None:
    source_id = "browser-disconnect"
    seed_source(tmp_path, source_id=source_id, platform="x")
    task = fetch_task(source_id)
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    calls = {"fetch": 0, "prepare": 0, "cleanup": 0}
    diagnostic = {"category": "browser_session", "summary": "browser disconnected"}

    def prepare(**_kwargs):
        calls["prepare"] += 1
        return {"status": "ready", "browser_headless": True}

    def fetch(*_args, **_kwargs):
        calls["fetch"] += 1
        if calls["fetch"] == 1:
            raise AcquisitionFailureError(
                AcquisitionFailure(
                    reason=FetchFailureReason.OPENCLI_DAEMON_DOWN,
                    message="browser disconnected",
                    retryable=True,
                    user_action=None,
                    diagnostic=diagnostic,
                )
            )
        return {"raw_item_ids": [], "card_build_task_ids": [], "fetched_count": 0}

    monkeypatch.setattr("backend.application.workflows.browser_acquisition_batch.prepare_opencli_browser_session", prepare)
    monkeypatch.setattr(
        "backend.application.workflows.browser_acquisition_batch.cleanup_headless_browser_session",
        lambda: calls.__setitem__("cleanup", calls["cleanup"] + 1),
    )
    # handle_fetch imports the workflow at call time, so inject at its durable
    # workflow boundary rather than at the handler module.
    monkeypatch.setattr("backend.application.workflows.acquisition.fetch_source", fetch)

    result = run_due_tasks(tmp_path, now="2026-07-11T00:00:00Z", task_types=[TaskType.FETCH])
    event_types = [event["event_type"] for event in TaskEventRepository(tmp_path).list_events(task_id=task.task_id)]
    repair = result["results"][0]["artifacts"]["browser_batch"]["repair_attempts"]

    assert result["succeeded"] == 1
    assert calls == {"fetch": 2, "prepare": 2, "cleanup": 1}
    assert repair[0]["original_diagnostic"] == diagnostic
    # Task events use compact UTC timestamps, so events emitted in the same
    # second are intentionally not a total-order API.  Verify the lifecycle
    # set and its exactly-once cleanup invariant instead.
    assert set(event_types) >= {
        "claimed", "browser_prepare_started", "browser_prepare_completed",
        "browser_repair_started", "browser_repair_completed", "completed", "browser_cleanup_completed",
    }
    assert event_types.count("browser_cleanup_completed") == 1
