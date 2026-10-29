"""Acceptance contracts for the shared browser acquisition lifecycle."""

from __future__ import annotations

from pathlib import Path
import threading
import time

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.application.workflows.acquisition_failures import AcquisitionFailure, AcquisitionFailureError
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.storage.repositories import SourceRepository


def _source(base_dir: Path, source_id: str, *, platform: str = "x") -> None:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": source_id,
            "platform": platform,
            "source_type": "creator" if platform == "x" else "feed",
            "identifier": source_id,
            "display_name": source_id,
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": source_id,
            "raw_metadata": {"fetch_rate_policy": {"enabled": False}},
        }
    )


def _enqueue_fetches(base_dir: Path, source_ids: list[str]) -> None:
    repo = TaskRepository.for_base_dir(base_dir)
    for source_id in source_ids:
        repo.enqueue(Task(f"fetch_{source_id}", TaskType.FETCH, "source", source_id))


def test_three_browser_sources_share_one_prepare_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch

    _enqueue_fetches(tmp_path, ["x_one", "x_two", "x_three"])
    for source_id in ("x_one", "x_two", "x_three"):
        _source(tmp_path, source_id)
    events: list[str] = []
    monkeypatch.setattr(browser_acquisition_batch, "prepare_opencli_browser_session", lambda **_kwargs: events.append("prepare") or {"status": "ready", "browser_headless": True})
    monkeypatch.setattr(browser_acquisition_batch, "cleanup_headless_browser_session", lambda: events.append("cleanup"))

    result = run_due_tasks(
        tmp_path,
        handlers={TaskType.FETCH: lambda task, _base_dir: {"status": "success", "artifacts": {"source_id": task.target_id}}},
    )

    assert result["succeeded"] == 3
    assert events == ["prepare", "cleanup"]


def test_browser_failure_does_not_cancel_public_source(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch

    _source(tmp_path, "x_failing")
    _source(tmp_path, "rss_public", platform="rss")
    _enqueue_fetches(tmp_path, ["x_failing", "rss_public"])
    monkeypatch.setattr(browser_acquisition_batch, "prepare_opencli_browser_session", lambda **_kwargs: {"status": "ready", "browser_headless": False})

    def handler(task: Task, _base_dir: Path):
        if task.target_id == "x_failing":
            raise RuntimeError("browser fetch failed")
        return {"status": "success", "artifacts": {"source_id": task.target_id}}

    result = run_due_tasks(tmp_path, handlers={TaskType.FETCH: handler})

    assert result["succeeded"] == 1
    assert TaskRepository.for_base_dir(tmp_path).get("fetch_rss_public").status == TaskStatus.SUCCESS


def test_cleanup_runs_after_worker_handler_exception(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch

    _source(tmp_path, "x_exception")
    _enqueue_fetches(tmp_path, ["x_exception"])
    events: list[str] = []
    monkeypatch.setattr(browser_acquisition_batch, "prepare_opencli_browser_session", lambda **_kwargs: {"status": "ready", "browser_headless": True})
    monkeypatch.setattr(browser_acquisition_batch, "cleanup_headless_browser_session", lambda: events.append("cleanup"))

    run_due_tasks(
        tmp_path,
        handlers={TaskType.FETCH: lambda *_args: (_ for _ in ()).throw(RuntimeError("worker exploded"))},
    )

    assert events == ["cleanup"]


def test_logged_out_source_is_blocked_without_browser_restart_loop(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import browser_acquisition_batch
    from backend.application.workflows import acquisition as acquisition_workflow

    # This journey starts after the auth recheck confirms the acquisition failure.
    monkeypatch.setattr("backend.application.tasks.auth_recheck.recheck_auth_failure",
                        lambda error, *args, **kwargs: error)
    _source(tmp_path, "x_logged_out")
    monkeypatch.setattr(
        "backend.application.workflows.auth_recovery.setup_state_path",
        lambda: tmp_path / "setup-state.json",
    )
    _enqueue_fetches(tmp_path, ["x_logged_out"])
    prepare_calls: list[str] = []
    monkeypatch.setattr(browser_acquisition_batch, "prepare_opencli_browser_session", lambda **_kwargs: prepare_calls.append("prepare") or {"status": "ready", "browser_headless": False})
    failure = AcquisitionFailure(
        reason=FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
        message="not logged in",
        retryable=False,
        user_action="log in",
        diagnostic={"category": "auth_required"},
    )
    monkeypatch.setattr(acquisition_workflow, "fetch_source", lambda *_args, **_kwargs: (_ for _ in ()).throw(AcquisitionFailureError(failure)))

    result = run_due_tasks(tmp_path)

    assert result["skipped"] == 0
    assert prepare_calls == ["prepare"]
    assert TaskRepository.for_base_dir(tmp_path).get("fetch_x_logged_out").status == TaskStatus.BLOCKED


def test_two_simultaneous_failures_share_one_batch_repair(tmp_path: Path, monkeypatch) -> None:
    from backend.application.workflows import acquisition as acquisition_workflow
    from backend.application.workflows import browser_acquisition_batch

    _source(tmp_path, "x_first")
    _source(tmp_path, "youtube_second", platform="youtube")
    _enqueue_fetches(tmp_path, ["x_first", "youtube_second"])
    prepare_calls: list[str] = []

    def prepare(**_kwargs):
        prepare_calls.append("prepare")
        if len(prepare_calls) == 2:
            time.sleep(0.08)
        return {"status": "ready", "browser_headless": False}

    monkeypatch.setattr(browser_acquisition_batch, "prepare_opencli_browser_session", prepare)
    failure = AcquisitionFailure(
        reason=FetchFailureReason.OPENCLI_DAEMON_DOWN,
        message="bridge disconnected",
        retryable=True,
        user_action="Reconnect the OpenCLI Browser Bridge.",
        diagnostic={"category": "browser_session"},
    )
    first_attempts = threading.Barrier(2)
    attempts: dict[str, int] = {"x_first": 0, "youtube_second": 0}
    guard = threading.Lock()

    def fetch_source(_base_dir: Path, *, source_id: str, **_kwargs):
        with guard:
            attempts[source_id] += 1
            attempt = attempts[source_id]
        if attempt == 1:
            first_attempts.wait(timeout=2)
            raise AcquisitionFailureError(failure)
        return {"raw_item_ids": []}

    monkeypatch.setattr(acquisition_workflow, "fetch_source", fetch_source)
    result = run_due_tasks(tmp_path)

    assert result["succeeded"] == 2
    assert attempts == {"x_first": 2, "youtube_second": 2}
    assert prepare_calls == ["prepare", "prepare"]
