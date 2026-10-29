from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from backend.application.tasks.planner import plan_initial_fetch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.handlers.acquisition import handle_fetch
from backend.application.workflows.acquisition import fetch_source
from backend.application.workflows.fetch_commit import SourceConfigurationChangedError
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import RawItemRepository, RunRepository, SourceCalibrationRepository, SourceCursorRepository, SourceRepository
from tests.infoos._factories import make_source_payload, card_count


class Connector:
    def __init__(self, result: FetchResult, *, during_fetch=None):
        self.result = result
        self.during_fetch = during_fetch
        self.calls: list[str | None] = []

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        self.calls.append(since_cursor)
        if self.during_fetch:
            self.during_fetch()
        return self.result


def source_payload(**overrides):
    payload = {
        "source_id": "rss_initial_phase1",
        "connector_id": "rss_initial_phase1",
        "identifier": "https://example.com/initial.xml",
        "connector_status": "implemented_uncalibrated",
        **overrides,
    }
    return make_source_payload(
        **payload,
    )


def item() -> RawItem:
    return RawItem(
        raw_item_id="initial-raw-1",
        connector_id="rss_initial_phase1",
        source_platform="rss",
        source_type="feed",
        source_url="https://example.com/item/1",
        captured_at="2026-07-13T00:00:00Z",
        raw_text="initial fetch payload",
        raw_metadata={"title": "Initial item"},
    )


def durable_task(tmp_path) -> Task:
    source = SourceRepository(tmp_path).get_source("rss_initial_phase1")
    task = plan_initial_fetch(source, now="2026-07-13T00:00:00Z")
    assert task is not None
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(task)
    claimed = repo.claim_task_ids(
        [task.task_id],
        worker_id="worker-phase1",
        now="2026-07-13T00:00:01Z",
        lease_expires_at="2026-07-13T00:10:01Z",
    )
    return claimed[0]


def counts(tmp_path):
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        return {
            table: conn.execute(f"select count(*) from {table}").fetchone()[0]
            for table in (
                "raw_items", "source_cursors", "source_calibrations",
                "run_history", "acquisition_checkpoints",
            )
        }


def test_ordinary_fetch_cannot_admit_uncalibrated_source(tmp_path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source(source_payload())
    source = SourceRepository(tmp_path).get_source("rss_initial_phase1")
    task = Task(
        task_id="ordinary",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="rss_initial_phase1",
        input_version=source["calibration_input_version"],
        claimed_by="worker",
        artifacts_summary={"source_input_version": source["calibration_input_version"]},
    )
    connector = Connector(FetchResult(items=[], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    with pytest.raises(ValueError, match="disabled or unsupported"):
        fetch_source(tmp_path, source_id=task.target_id, task=task)

    assert connector.calls == []


def test_stale_initial_fetch_skips_before_connector_io(tmp_path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source(source_payload())
    task = plan_initial_fetch(SourceRepository(tmp_path).get_source("rss_initial_phase1"))
    SourceRepository(tmp_path).upsert_source(source_payload(identifier="https://example.com/edited.xml"))
    connector = Connector(FetchResult(items=[], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    result = handle_fetch(task, tmp_path)

    assert result["status"] == TaskStatus.SKIPPED.value
    assert result["artifacts"]["reason"] == "source_configuration_changed"
    assert connector.calls == []


@pytest.mark.parametrize("items,next_cursor", [([item()], "cursor-after"), ([], "must-clear")])
def test_initial_fetch_commits_promotion_evidence_schedule_run_and_checkpoint(
    tmp_path, monkeypatch, items, next_cursor
) -> None:
    SourceRepository(tmp_path).upsert_source(source_payload())
    SourceCursorRepository(tmp_path).upsert(
        source_id="rss_initial_phase1",
        connector_id="rss_initial_phase1",
        cursor_value="old-cursor",
        last_run_id="old-run",
    )
    task = durable_task(tmp_path)
    connector = Connector(FetchResult(items=items, next_cursor=None if not items else next_cursor))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    monkeypatch.setattr("backend.application.workflows.acquisition.utc_now_seconds_z", lambda: "2026-07-13T00:00:02Z")

    artifacts = fetch_source(tmp_path, source_id=task.target_id, task=task)

    assert connector.calls == [None]
    assert artifacts["initial_fetch"] is True
    assert SourceRepository(tmp_path).get_source(task.target_id)["connector_status"] == "supported"
    assert SourceCursorRepository(tmp_path).get(task.target_id)["cursor_value"] == (next_cursor if items else None)
    assert SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)["result"] == "success"
    assert len(RunRepository(tmp_path).list_runs()) == 1
    assert counts(tmp_path)["acquisition_checkpoints"] == 1
    assert len(artifacts["card_build_task_ids"]) == len(items)


def test_xiaohongshu_empty_initial_fetch_is_supported_successful_no_data(tmp_path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source(
        source_payload(platform="xiaohongshu", source_type="keyword", identifier="AI")
    )
    task = durable_task(tmp_path)
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda *_: Connector(FetchResult(items=[], next_cursor="must-clear", visible_count=0)),
    )

    artifacts = fetch_source(tmp_path, source_id=task.target_id, task=task)

    source = SourceRepository(tmp_path).get_source(task.target_id)
    evidence = SourceCalibrationRepository(tmp_path).get_by_task_id(task.task_id)
    assert artifacts["no_data"] is True
    assert artifacts["raw_item_ids"] == []
    assert artifacts["card_build_task_ids"] == []
    assert source["connector_status"] == "supported"
    assert evidence["result"] == "success"
    assert evidence["raw_payload"]["no_data"] is True
    assert card_count(tmp_path) == 0


def test_edit_during_io_rolls_back_every_durable_write(tmp_path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source(source_payload())
    task = durable_task(tmp_path)

    def edit_source():
        SourceRepository(tmp_path).upsert_source(source_payload(identifier="https://example.com/edited.xml"))

    connector = Connector(FetchResult(items=[item()], next_cursor="cursor-after"), during_fetch=edit_source)
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    with pytest.raises(SourceConfigurationChangedError):
        fetch_source(tmp_path, source_id=task.target_id, task=task)

    assert counts(tmp_path) == {
        "raw_items": 0, "source_cursors": 0, "source_calibrations": 0,
        "run_history": 0, "acquisition_checkpoints": 0,
    }
    assert SourceRepository(tmp_path).get_source(task.target_id)["connector_status"] == "implemented_uncalibrated"


def test_checkpoint_replay_never_refetches_or_mutates_newer_source(tmp_path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source(source_payload())
    task = durable_task(tmp_path)
    first_connector = Connector(FetchResult(items=[], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: first_connector)
    committed = fetch_source(tmp_path, source_id=task.target_id, task=task)
    SourceRepository(tmp_path).upsert_source(
        source_payload(identifier="https://example.com/newer.xml", connector_status="implemented_uncalibrated")
    )
    newer = SourceRepository(tmp_path).get_source(task.target_id)
    forbidden = Connector(FetchResult(items=[item()], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: forbidden)

    replay = fetch_source(tmp_path, source_id=task.target_id, task=task)

    assert replay["replayed"] is True
    assert replay["run_id"] == committed["run_id"]
    assert forbidden.calls == []
    current = SourceRepository(tmp_path).get_source(task.target_id)
    assert current["identifier"] == newer["identifier"]
    assert current["connector_status"] == "implemented_uncalibrated"
    assert current["next_run_at"] == newer["next_run_at"]
    assert counts(tmp_path)["source_calibrations"] == 1


def test_run_history_failure_rolls_back_initial_fetch(tmp_path, monkeypatch) -> None:
    SourceRepository(tmp_path).upsert_source(source_payload())
    task = durable_task(tmp_path)
    connector = Connector(FetchResult(items=[item()], next_cursor="cursor-after"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    monkeypatch.setattr(
        RunRepository,
        "append_to_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected run failure")),
    )

    with pytest.raises(RuntimeError, match="injected run failure"):
        fetch_source(tmp_path, source_id=task.target_id, task=task)

    assert counts(tmp_path) == {
        "raw_items": 0, "source_cursors": 0, "source_calibrations": 0,
        "run_history": 0, "acquisition_checkpoints": 0,
    }


def test_direct_diagnostic_edit_during_io_has_no_durable_writes(tmp_path, monkeypatch) -> None:
    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    before = SourceRepository(tmp_path).get_source("rss_initial_phase1")

    def edit_source():
        edited = source_payload(identifier="https://example.com/direct-edit.xml")
        edited["connector_status"] = "supported"
        SourceRepository(tmp_path).upsert_source(edited)

    connector = Connector(FetchResult(items=[item()], next_cursor="cursor"), during_fetch=edit_source)
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)
    task = Task(
        task_id="direct-diagnostic",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="rss_initial_phase1",
    )

    with pytest.raises(SourceConfigurationChangedError):
        fetch_source(tmp_path, source_id=task.target_id, task=task)

    assert counts(tmp_path) == {
        "raw_items": 0, "source_cursors": 0, "source_calibrations": 0,
        "run_history": 0, "acquisition_checkpoints": 0,
    }
    current = SourceRepository(tmp_path).get_source(task.target_id)
    assert current["last_run_at"] == before["last_run_at"]
    assert current["next_run_at"] == before["next_run_at"]


def test_unchanged_running_legacy_v1_backfills_version_before_io(tmp_path, monkeypatch) -> None:
    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    legacy = Task(
        task_id="legacy-v1-fetch",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="rss_initial_phase1",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(legacy)
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute(
            "update sources set calibration_input_version = null where source_id = ?",
            (legacy.target_id,),
        )
    claimed = repo.claim_task_ids(
        [legacy.task_id], worker_id="legacy-worker", now="2026-07-13T00:00:00Z",
        lease_expires_at="2026-07-13T00:10:00Z",
    )[0]
    connector = Connector(FetchResult(items=[], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    fetch_source(tmp_path, source_id=claimed.target_id, task=claimed)

    upgraded = repo.get(legacy.task_id)
    assert upgraded.input_version != "v1"
    assert upgraded.artifacts_summary["source_input_version"] == upgraded.input_version
    assert connector.calls == [None]
    assert SourceRepository(tmp_path).get_source(legacy.target_id)["calibration_input_version"] == upgraded.input_version


def test_two_pending_legacy_v1_tasks_backfill_when_claimed_after_schedule_update(tmp_path, monkeypatch) -> None:
    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    first = Task(task_id="legacy-v1-first", task_type=TaskType.FETCH, target_kind="source", target_id=source["source_id"])
    second = Task(task_id="legacy-v1-second", task_type=TaskType.FETCH, target_kind="source", target_id=source["source_id"])
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(first)
    repo.enqueue(second)
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute("update sources set calibration_input_version = null where source_id = ?", (source["source_id"],))
    connector = Connector(FetchResult(items=[], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    claimed_first = repo.claim_task_ids(
        [first.task_id], worker_id="legacy-worker-1", now="2026-07-13T00:00:00Z",
        lease_expires_at="2026-07-13T00:10:00Z",
    )[0]
    fetch_source(tmp_path, source_id=claimed_first.target_id, task=claimed_first)
    claimed_second = repo.claim_task_ids(
        [second.task_id], worker_id="legacy-worker-2", now="2026-07-13T00:01:00Z",
        lease_expires_at="2026-07-13T00:11:00Z",
    )[0]
    fetch_source(tmp_path, source_id=claimed_second.target_id, task=claimed_second)

    upgraded_second = repo.get(second.task_id)
    assert upgraded_second.input_version != "v1"
    assert upgraded_second.artifacts_summary["source_input_version"] == upgraded_second.input_version
    assert connector.calls == [None, None]


def test_material_edit_invalidates_legacy_v1_before_io_and_repromotion(tmp_path) -> None:
    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    legacy = Task(
        task_id="legacy-v1-stale",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="rss_initial_phase1",
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(legacy)

    edited = source_payload(identifier="https://example.com/material-edit.xml")
    SourceRepository(tmp_path).upsert_source(edited)
    edited["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(edited)

    stale = repo.get(legacy.task_id)
    assert stale.status == TaskStatus.SKIPPED
    assert stale.error_reason == "source_configuration_changed"
    assert stale.artifacts_summary["reason"] == "source_configuration_changed"


def test_diagnostic_wrappers_deny_uncalibrated_without_writes(tmp_path, monkeypatch) -> None:
    from backend.application.workflows.fetch import fetch
    from backend.application.workflows.run_once import run_once

    SourceRepository(tmp_path).upsert_source(source_payload())
    connector = Connector(FetchResult(items=[item()], next_cursor="cursor"))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    fetch_result = fetch(tmp_path)
    run_result = run_once(tmp_path)

    assert fetch_result["skipped_sources"] == ["rss_initial_phase1"]
    assert run_result["sources"][0]["status"] == "skipped"
    assert connector.calls == []
    assert counts(tmp_path) == {
        "raw_items": 0, "source_cursors": 0, "source_calibrations": 0,
        "run_history": 0, "acquisition_checkpoints": 0,
    }


def test_run_once_rejects_connector_override_without_writes(tmp_path, monkeypatch) -> None:
    from backend.application.workflows.run_once import run_once

    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda *_: (_ for _ in ()).throw(AssertionError("override diagnostic must not fetch")),
    )

    result = run_once(
        tmp_path,
        source_overrides={"rss_initial_phase1": {"identifier": "https://override.invalid"}},
    )

    assert result["sources"][0]["status"] == "skipped"
    assert counts(tmp_path) == {
        "raw_items": 0, "source_cursors": 0, "source_calibrations": 0,
        "run_history": 0, "acquisition_checkpoints": 0,
    }


def test_fetch_wrapper_stale_during_io_has_no_writes(tmp_path, monkeypatch) -> None:
    from backend.application.workflows.fetch import fetch

    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)

    def edit_source():
        edited = source_payload(identifier="https://example.com/wrapper-edit.xml")
        edited["connector_status"] = "supported"
        SourceRepository(tmp_path).upsert_source(edited)

    connector = Connector(FetchResult(items=[item()], next_cursor="cursor"), during_fetch=edit_source)
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    result = fetch(tmp_path)

    assert result["skipped_sources"] == ["rss_initial_phase1"]
    assert connector.calls == [None]
    assert counts(tmp_path) == {
        "raw_items": 0, "source_cursors": 0, "source_calibrations": 0,
        "run_history": 0, "acquisition_checkpoints": 0,
    }


def test_legacy_v1_backfill_cannot_mutate_replacement_lease(tmp_path, monkeypatch) -> None:
    source = source_payload()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    repo = TaskRepository.for_base_dir(tmp_path)
    legacy = Task(
        task_id="legacy-v1-race", task_type=TaskType.FETCH,
        target_kind="source", target_id="rss_initial_phase1",
    )
    repo.enqueue(legacy)
    claimed = repo.claim_task_ids(
        [legacy.task_id], worker_id="old-worker", now="2026-07-13T00:00:00Z",
        lease_expires_at="2026-07-13T00:10:00Z",
    )[0]
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute(
            """
            update tasks set claimed_by = 'replacement-worker', attempt = 2,
              artifacts_summary = '{"replacement": true}' where task_id = ?
            """,
            (legacy.task_id,),
        )
    connector = Connector(FetchResult(items=[], next_cursor=None))
    monkeypatch.setattr("backend.application.workflows.acquisition._build_connector", lambda *_: connector)

    with pytest.raises(SourceConfigurationChangedError):
        fetch_source(tmp_path, source_id=claimed.target_id, task=claimed)

    replacement = repo.get(legacy.task_id)
    assert replacement.claimed_by == "replacement-worker"
    assert replacement.attempt == 2
    assert replacement.input_version == "v1"
    assert replacement.artifacts_summary == {"replacement": True}
    assert connector.calls == []


def test_raw_metadata_string_roundtrip_has_stable_fingerprint(tmp_path) -> None:
    from backend.domain.sources.calibration import source_calibration_version

    payload = source_payload(raw_metadata='{"topics":["a"],"nested":{"x":1}}')
    before = source_calibration_version(payload)
    SourceRepository(tmp_path).upsert_source(payload)
    stored = SourceRepository(tmp_path).get_source("rss_initial_phase1")

    assert stored["raw_metadata"] == {"topics": ["a"], "nested": {"x": 1}}
    assert stored["calibration_input_version"] == before
    assert source_calibration_version(stored) == before
