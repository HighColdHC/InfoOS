"""Acceptance coverage for replay-safe source acquisition."""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.batches import create_task_batch, get_task_batch
from backend.application.tasks.worker import run_tasks_by_ids
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import RawItemRepository, SourceCursorRepository, SourceRepository


class ReplayConnector:
    connector_id = "replay_connector"

    def __init__(self, acquisition=None) -> None:
        self.acquisition = acquisition
        self.cursors: list[str | None] = []

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        self.cursors.append(since_cursor)
        return FetchResult(items=[_raw_item()], next_cursor="remote-1", visible_count=1, acquisition=self.acquisition)


def _raw_item() -> RawItem:
    return RawItem(
        raw_item_id="remote-1",
        connector_id="replay_connector",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/remote-1",
        captured_at="2026-07-10T08:00:00Z",
        raw_text="durable replay item",
        raw_metadata={"source_id": "replay_source", "title": "Replay"},
    )


def _seed_source(base_dir: Path) -> Task:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": "replay_source",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Replay source",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "replay_connector",
        }
    )
    source = SourceRepository(base_dir).get_source("replay_source")
    version = source["calibration_input_version"]
    return Task(
        task_id="fetch_replay_source",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="replay_source",
        input_version=version,
        artifacts_summary={
            "run_id": "run-replay",
            "chain_id": "chain-replay",
            "source_input_version": version,
        },
    )


def _fetch(base_dir: Path, task: Task):
    from backend.application.workflows.acquisition import fetch_source

    return fetch_source(base_dir, source_id=task.target_id, task=task)


def _counts(base_dir: Path) -> dict[str, int]:
    with sqlite3.connect(base_dir / "data" / "infoos.sqlite3") as conn:
        return {
            table: int(conn.execute(f"select count(*) from {table}").fetchone()[0])
            for table in ("raw_items", "source_cursors", "tasks", "cards")
        }


def _card_build_task_count(base_dir: Path) -> int:
    with sqlite3.connect(base_dir / "data" / "infoos.sqlite3") as conn:
        return int(conn.execute("select count(*) from tasks where task_type = 'card_build'").fetchone()[0])


def test_crash_before_commit_replays_same_remote_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    original_commit = acquisition.commit_fetch_result
    monkeypatch.setattr(acquisition, "commit_fetch_result", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash before commit")))

    with pytest.raises(RuntimeError, match="crash before commit"):
        _fetch(tmp_path, task)

    assert _counts(tmp_path) == {"raw_items": 0, "source_cursors": 0, "tasks": 0, "cards": 0}
    assert connector.cursors == [None]

    monkeypatch.setattr(acquisition, "commit_fetch_result", original_commit)
    artifacts = _fetch(tmp_path, task)

    assert connector.cursors == [None, None]
    assert artifacts["raw_item_ids"] == ["remote-1"]
    assert _counts(tmp_path) == {"raw_items": 1, "source_cursors": 1, "tasks": 1, "cards": 0}


def test_crash_after_commit_retries_card_build_without_refetch_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    artifacts = _fetch(tmp_path, task)
    card_task_id = artifacts["card_build_task_ids"][0]
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    TaskRepository.for_base_dir(tmp_path).update_status(task.task_id, TaskStatus.SUCCESS)

    first = run_tasks_by_ids(
        tmp_path,
        [card_task_id],
        now="2026-07-10T08:01:00Z",
        handlers={TaskType.CARD_BUILD: lambda *_args: (_ for _ in ()).throw(RuntimeError("card worker crashed"))},
    )

    assert first["requeued"] == 1
    assert TaskRepository.for_base_dir(tmp_path).get(card_task_id).status == TaskStatus.PENDING
    assert connector.cursors == [None]
    assert RawItemRepository(tmp_path).get("remote-1") is not None

    second = run_tasks_by_ids(tmp_path, [card_task_id], now="2026-07-10T08:02:00Z")

    assert second["succeeded"] == 1
    assert connector.cursors == [None]
    assert _counts(tmp_path)["raw_items"] == 1
    assert _counts(tmp_path)["cards"] == 1
    assert _card_build_task_count(tmp_path) == 1


@pytest.mark.parametrize("partial", [False, True])
def test_worker_reuses_committed_acquisition_checkpoint_after_finalize_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    partial: bool,
) -> None:
    """A crash after acquisition commit must not repeat remote connector I/O."""
    from backend.application.workflows import acquisition
    task = _seed_source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    batch = create_task_batch(tmp_path, batch_type="source_run", task_ids=[task.task_id])
    meta = {"requested_count": 40, "observed_count": 1, "complete": False,
            "stop_reason": "no_progress", "stop_position": 1} if partial else None
    connector = ReplayConnector(meta)
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    original_finalize = TaskRepository.finalize_terminal_with_downstream
    finalize_calls = 0

    def fail_once(self, *args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise RuntimeError("crash after acquisition commit")
        return original_finalize(self, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "finalize_terminal_with_downstream", fail_once)
    first = run_tasks_by_ids(tmp_path, [task.task_id], now="2026-07-10T08:00:00Z")

    assert first["requeued"] == 1
    pending = repo.get(task.task_id)
    assert pending is not None
    checkpoint = pending.artifacts_summary["acquisition_checkpoint"]
    assert checkpoint["raw_item_ids"] == ["remote-1"]
    assert checkpoint["cursor_after"] == "remote-1"
    card_task_id = checkpoint["card_build_task_ids"][0]
    card_task = repo.get(card_task_id)
    assert card_task is not None
    assert [(dep.task_id, dep.required_status) for dep in card_task.depends_on] == [
        (task.task_id, TaskStatus.SUCCESS)
    ]
    assert run_tasks_by_ids(tmp_path, [card_task_id], now="2026-07-10T08:30:00Z")["processed"] == 0

    second = run_tasks_by_ids(tmp_path, [task.task_id], now=str(pending.next_run_at))

    assert second["succeeded"] == 1
    assert connector.cursors == [None]
    assert _counts(tmp_path)["raw_items"] == 1
    assert _card_build_task_count(tmp_path) == 1
    loaded_batch = get_task_batch(tmp_path, batch["batch_id"])
    assert loaded_batch is not None
    assert card_task_id in loaded_batch["task_ids"]
    if partial:
        assert checkpoint["acquisition"] == meta
        assert checkpoint["completeness_status"] == "partial"
        assert repo.get(task.task_id).artifacts_summary["completeness_status"] == "partial"
        assert repo.get(task.task_id).artifacts_summary["acquisition"] == meta
        assert run_tasks_by_ids(tmp_path, [card_task_id], now="2026-07-10T09:00:00Z")["succeeded"] == 1
        assert _counts(tmp_path)["cards"] == 1
        assert connector.cursors == [None]
        from backend.read_models.source_run_list import read_source_run_page
        from backend.read_models.source_run_trace import read_source_run_trace
        from backend.read_models.task_queue_tracking import task_tracking_page
        from backend.read_models.source_management import read_source_detail
        page = read_source_run_page(tmp_path, execution_scope="executed")
        assert page["items"][0]["completeness_status"] == "partial"
        trace = read_source_run_trace(tmp_path, page["items"][0]["source_run_id"], item_limit=10, cursor=None)
        assert trace["run"]["acquisition"] == meta
        assert trace["task_tracking"][0]["completeness_status"] == "partial"
        fetch = next(row for row in task_tracking_page(tmp_path)["items"] if row["task_type"] == "fetch")
        assert fetch["observed_count"] == 1
        assert fetch["requested_count"] == 40
        assert fetch["stop_reason"] == "no_progress"
        detail = read_source_detail(tmp_path, task.target_id)
        assert detail["recent_run_stats"]["completeness_status"] == "partial"


def test_checkpoint_replay_does_not_duplicate_run_after_schedule_update_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    original_finalize = TaskRepository.finalize_terminal_with_downstream
    finalize_calls = 0

    def fail_once(self, *args, **kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        if finalize_calls == 1:
            raise RuntimeError("crash after run history")
        return original_finalize(self, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "finalize_terminal_with_downstream", fail_once)
    first = run_tasks_by_ids(tmp_path, [task.task_id], now="2026-07-10T08:00:00Z")
    pending = repo.get(task.task_id)

    assert first["requeued"] == 1
    assert pending is not None
    second = run_tasks_by_ids(tmp_path, [task.task_id], now=str(pending.next_run_at))

    assert second["succeeded"] == 1
    assert connector.cursors == [None]
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        assert conn.execute(
            "select count(*) from run_history where run_id = ?",
            ("run-replay",),
        ).fetchone()[0] == 1


def test_acquisition_commit_rolls_back_when_fetch_lease_owner_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    claimed = repo.claim_task_ids(
        [task.task_id],
        worker_id="current-worker",
        now="2026-07-10T08:00:00Z",
        lease_expires_at="2026-07-10T08:05:00Z",
    )[0]
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)

    with pytest.raises(RuntimeError, match="fetch_checkpoint_lease_lost"):
        _fetch(tmp_path, replace(claimed, claimed_by="stale-worker"))

    assert _counts(tmp_path) == {"raw_items": 0, "source_cursors": 0, "tasks": 1, "cards": 0}
    assert repo.get(task.task_id).artifacts_summary == task.artifacts_summary


def test_same_fetch_result_twice_creates_one_raw_item_and_one_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)

    first = _fetch(tmp_path, task)
    second_task = Task(
        task_id="fetch_replay_source_retry",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="replay_source",
        artifacts_summary={"run_id": "run-replay-retry", "chain_id": "chain-replay-retry"},
    )
    second = _fetch(tmp_path, second_task)
    card_task_id = first["card_build_task_ids"][0]
    TaskRepository.for_base_dir(tmp_path).enqueue(task)
    TaskRepository.for_base_dir(tmp_path).update_status(task.task_id, TaskStatus.SUCCESS)
    completed = run_tasks_by_ids(tmp_path, [card_task_id], now="2026-07-10T08:01:00Z")

    assert second["raw_item_ids"] == first["raw_item_ids"]
    assert second["card_build_task_ids"] == first["card_build_task_ids"]
    assert completed["succeeded"] == 1
    assert _counts(tmp_path)["raw_items"] == 1
    assert _counts(tmp_path)["cards"] == 1
    assert _card_build_task_count(tmp_path) == 1


def test_cursor_advances_only_after_card_build_task_is_durable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.application.tasks.repository import TaskRepository as QueueRepository
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    original_enqueue_many = QueueRepository.enqueue_many

    def crash_after_enqueue(self, tasks, *, conn=None):
        original_enqueue_many(self, tasks, conn=conn)
        raise RuntimeError("crash before cursor")

    monkeypatch.setattr(QueueRepository, "enqueue_many", crash_after_enqueue)

    with pytest.raises(RuntimeError, match="crash before cursor"):
        _fetch(tmp_path, task)

    assert SourceCursorRepository(tmp_path).get("replay_source") is None
    assert _counts(tmp_path) == {"raw_items": 0, "source_cursors": 0, "tasks": 0, "cards": 0}


def _commit_then_fail_finalize(tmp_path, monkeypatch):
    from backend.application.workflows import acquisition

    task = _seed_source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    connector = ReplayConnector()
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: connector)
    original_finalize = TaskRepository.finalize_terminal_with_downstream
    calls = 0

    def fail_once(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("finalize gap")
        return original_finalize(self, *args, **kwargs)

    monkeypatch.setattr(TaskRepository, "finalize_terminal_with_downstream", fail_once)
    first = run_tasks_by_ids(tmp_path, [task.task_id], now="2026-07-10T08:00:00Z")
    assert first["requeued"] == 1
    return task, repo, connector


def test_complete_checkpoint_replays_before_failed_browser_preparation(tmp_path, monkeypatch) -> None:
    from backend.application.tasks import candidate_lane_cache, continuous_browser
    from backend.application.tasks.lanes import ExecutionLane
    from backend.infrastructure.storage.runtime_circuits import (
        CircuitPolicy,
        RuntimeCircuitRepository,
    )

    task, repo, connector = _commit_then_fail_finalize(tmp_path, monkeypatch)
    RuntimeCircuitRepository(tmp_path).record_failure(
        "platform",
        "rss",
        occurred_at="2026-07-10T08:00:01Z",
        policy=CircuitPolicy(failure_threshold=1, cooldown_seconds=3600),
    )
    monkeypatch.setattr(candidate_lane_cache, "execution_lane_and_site_for",
                        lambda *_args: (ExecutionLane.BROWSER_ACQUISITION, "rss"))

    @contextmanager
    def forbidden_browser_prepare(*_args, **_kwargs):
        raise AssertionError("complete checkpoint must bypass browser preparation")
        yield

    monkeypatch.setattr(continuous_browser, "browser_acquisition_batch", forbidden_browser_prepare)
    pending = repo.get(task.task_id)
    second = run_tasks_by_ids(tmp_path, [task.task_id], now=str(pending.next_run_at))

    assert second["succeeded"] == 1
    assert connector.cursors == [None]


def test_complete_legacy_artifact_checkpoint_is_validated_and_upgraded(tmp_path, monkeypatch) -> None:
    task, repo, connector = _commit_then_fail_finalize(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute("delete from acquisition_checkpoints where task_id = ?", (task.task_id,))
    pending = repo.get(task.task_id)

    second = run_tasks_by_ids(tmp_path, [task.task_id], now=str(pending.next_run_at))

    assert second["succeeded"] == 1
    assert connector.cursors == [None]
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        assert conn.execute(
            "select count(*) from acquisition_checkpoints where task_id = ?", (task.task_id,)
        ).fetchone()[0] == 1


def test_incomplete_legacy_artifact_checkpoint_reruns_with_recovery_run(tmp_path, monkeypatch) -> None:
    task, repo, connector = _commit_then_fail_finalize(tmp_path, monkeypatch)
    pending = repo.get(task.task_id)
    artifacts = dict(pending.artifacts_summary)
    legacy = dict(artifacts["acquisition_checkpoint"])
    legacy.pop("source_input_version")
    artifacts["acquisition_checkpoint"] = legacy
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute("delete from acquisition_checkpoints where task_id = ?", (task.task_id,))
        conn.execute(
            "update tasks set artifacts_summary = ? where task_id = ?",
            (json.dumps(artifacts), task.task_id),
        )
    pending = repo.get(task.task_id)

    second = run_tasks_by_ids(tmp_path, [task.task_id], now=str(pending.next_run_at))

    assert second["succeeded"] == 1
    assert connector.cursors == [None, "remote-1"]
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        run_ids = [row[0] for row in conn.execute("select run_id from run_history order by run_id")]
    assert any("_recovery_" in run_id for run_id in run_ids)
