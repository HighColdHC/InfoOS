from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from backend.application.tasks.executor import run_tasks_by_ids
from backend.app.main import create_app
from backend.application.tasks.planner import plan_initial_fetch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.usecases.source_configuration_lifecycle import update_source_configuration
from backend.application.usecases.task_retry import retry_task
from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
from backend.application.workflows.setup_first_fetch import submit_first_fetch
from backend.application.workflows.source_initial_fetch import enqueue_or_reuse_initial_fetch
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import SourceRepository
from backend.read_models.source_management import read_source_detail
from backend.read_models.source_management_page import _current_calibrations
from backend.infrastructure.storage.database import connect_readonly, default_database_path
from tests.infoos._factories import make_source_payload, make_x_source_payload


def _source(**overrides):
    payload = make_source_payload(
        source_id="rss_phase2",
        connector_id="rss_phase2",
        identifier="https://example.com/phase2.xml",
        **overrides,
    )
    payload["connector_status"] = overrides.get("connector_status", "implemented_uncalibrated")
    return payload


def _legacy(source: dict, *, task_id: str = "source_calibrate_rss_phase2_legacy") -> Task:
    return Task(
        task_id=task_id,
        task_type=TaskType.SOURCE_CALIBRATE,
        target_kind="source",
        target_id=str(source["source_id"]),
        input_version=str(source["calibration_input_version"]),
        artifacts_summary={"chain_id": f"legacy_{source['source_id']}"},
    )


def _stored(tmp_path, payload=None):
    SourceRepository(tmp_path).upsert_source(payload or _source())
    return SourceRepository(tmp_path).get_source((payload or {"source_id": "rss_phase2"})["source_id"])


def test_shared_enqueue_reuses_active_legacy_without_creating_initial(tmp_path) -> None:
    source = _stored(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    legacy = _legacy(source)
    repo.enqueue(legacy)

    queued = enqueue_or_reuse_initial_fetch(
        tmp_path, source, now="2026-07-14T00:00:00Z", reason="test"
    )

    assert queued["task_id"] == legacy.task_id
    assert queued["task_type"] == TaskType.SOURCE_CALIBRATE.value
    assert queued["initial_fetch"] is False
    assert repo.list_by_target(
        target_kind="source", target_id=source["source_id"], task_type=TaskType.FETCH
    ) == []


def test_concurrent_lifecycle_and_reconcile_create_one_initial_fetch(tmp_path) -> None:
    source = _stored(tmp_path)

    def lifecycle():
        return update_source_configuration(
            tmp_path,
            source["source_id"],
            {**source, "display_name": "Concurrent edit"},
            now="2026-07-14T00:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        lifecycle_result, reconcile_result = [
            future.result()
            for future in (
                executor.submit(lifecycle),
                executor.submit(reconcile_pipeline, tmp_path, limit=10),
            )
        ]

    tasks = TaskRepository.for_base_dir(tmp_path).list_by_target(
        target_kind="source", target_id=source["source_id"], task_type=TaskType.FETCH
    )
    assert len([task for task in tasks if task.artifacts_summary.get("initial_fetch")]) == 1
    assert lifecycle_result["task_ids"] or reconcile_result["created_by_type"].get("fetch") == 1


@pytest.mark.parametrize("entry", ["legacy", "initial"])
def test_both_handlers_choose_same_winner_before_connector_io(tmp_path, monkeypatch, entry) -> None:
    source = _stored(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    legacy = _legacy(source)
    initial = plan_initial_fetch(source, now="2026-07-14T00:00:00Z")
    assert initial is not None
    repo.enqueue(legacy)
    repo.enqueue(initial)
    winner = initial if entry == "legacy" else legacy
    loser = legacy if entry == "legacy" else initial
    with sqlite3.connect(tmp_path / "data" / "infoos.sqlite3") as conn:
        conn.execute("update tasks set created_at = '2020-01-01T00:00:00Z' where task_id = ?", (winner.task_id,))
        conn.execute("update tasks set created_at = '2021-01-01T00:00:00Z' where task_id = ?", (loser.task_id,))
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy probe must not run")),
    )
    monkeypatch.setattr(
        "backend.application.workflows.acquisition._build_connector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("initial connector must not run")),
    )

    result = run_tasks_by_ids(tmp_path, [loser.task_id], now="2026-07-14T00:00:01Z")

    assert result["skipped"] == 1
    skipped = repo.get(loser.task_id)
    assert skipped.status == TaskStatus.SKIPPED
    assert skipped.artifacts_summary["reason"] == "superseded"
    assert skipped.artifacts_summary["superseded_by"] == winner.task_id


def test_terminal_legacy_retry_redirects_to_active_initial_and_never_reprobes(tmp_path, monkeypatch) -> None:
    source = _stored(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    legacy = _legacy(source)
    repo.enqueue(legacy)
    repo.update_status(legacy.task_id, TaskStatus.BLOCKED)
    queued = enqueue_or_reuse_initial_fetch(
        tmp_path, source, now="2026-07-14T00:00:00Z", reason="manual"
    )
    monkeypatch.setattr(
        "backend.application.workflows.source_calibration.probe_source_for_calibration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("terminal retry must not probe")),
    )

    result = retry_task(tmp_path, legacy.task_id)

    assert result["redirected"] is True
    assert result["task_id"] == queued["task_id"]
    assert repo.get(legacy.task_id).status == TaskStatus.BLOCKED


def test_active_legacy_retry_reuses_original_task(tmp_path) -> None:
    source = _stored(tmp_path)
    legacy = _legacy(source)
    TaskRepository.for_base_dir(tmp_path).enqueue(legacy)

    result = retry_task(tmp_path, legacy.task_id)

    assert result["task_id"] == legacy.task_id
    assert result["reused_active_legacy"] is True


def test_setup_first_fetch_reuses_uncalibrated_initial(tmp_path) -> None:
    source = make_x_source_payload(
        source_id="x_phase2", connector_id="x_phase2", identifier="openai"
    )
    SourceRepository(tmp_path).upsert_source(source)
    stored = SourceRepository(tmp_path).get_source("x_phase2")
    queued = enqueue_or_reuse_initial_fetch(
        tmp_path, stored, now="2026-07-14T00:00:00Z", reason="preexisting"
    )

    result = submit_first_fetch(tmp_path)

    assert result["ok"] is True
    assert result["batch"]["task_ids"] == [queued["task_id"]]
    task = TaskRepository.for_base_dir(tmp_path).get(str(queued["task_id"]))
    assert task.artifacts_summary["setup_first_fetch"] is True


def test_dual_read_prefers_current_initial_over_legacy(tmp_path) -> None:
    source = _stored(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    legacy = _legacy(source)
    initial = plan_initial_fetch(source, now="2026-07-14T00:00:00Z")
    assert initial is not None
    repo.enqueue(legacy)
    repo.enqueue(initial)

    with connect_readonly(default_database_path(tmp_path)) as conn:
        current = _current_calibrations(conn, [source])[source["source_id"]]
    assert current.task_id == initial.task_id
    assert read_source_detail(tmp_path, source["source_id"])["source"]["readiness_status"] == "awaiting_initial_fetch"


def test_lifecycle_response_with_active_legacy_preserves_compatibility_fields(tmp_path) -> None:
    source = _stored(tmp_path)
    legacy = _legacy(source)
    TaskRepository.for_base_dir(tmp_path).enqueue(legacy)

    result = update_source_configuration(
        tmp_path,
        source["source_id"],
        {**source, "display_name": "Cosmetic"},
        now="2026-07-14T00:00:00Z",
    )

    assert result["queue_status"] == "calibration_pending"
    assert result["calibration_task_id"] == legacy.task_id
    assert result["initial_fetch_task_id"] is None
    assert result["initial_fetch_queue_status"] is None


def test_material_update_api_returns_initial_fetch_compatibility_alias(tmp_path) -> None:
    source = _source()
    source["connector_status"] = "supported"
    SourceRepository(tmp_path).upsert_source(source)
    client = TestClient(create_app(tmp_path))

    response = client.put(
        "/api/configurable-sources/rss_phase2",
        json={
            "source_id": "rss_phase2",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/material-new.xml",
            "display_name": "Material edit",
            "cadence_hours": 24,
            "enabled": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["queue_status"] == "calibration_pending"
    assert body["initial_fetch_queue_status"] == "pending"
    assert body["calibration_task_id"] == body["initial_fetch_task_id"]
    task = TaskRepository.for_base_dir(tmp_path).get(body["initial_fetch_task_id"])
    assert task.task_type == TaskType.FETCH
    assert task.artifacts_summary["initial_fetch"] is True


def test_manual_run_api_reuses_uncalibrated_initial_fetch(tmp_path) -> None:
    source = _stored(tmp_path)
    queued = enqueue_or_reuse_initial_fetch(
        tmp_path, source, now="2026-07-14T00:00:00Z", reason="preexisting"
    )
    client = TestClient(create_app(tmp_path))

    response = client.post(f"/api/configurable-sources/{source['source_id']}/run")

    assert response.status_code == 202
    assert response.json()["batch"]["task_ids"] == [queued["task_id"]]


def test_terminal_legacy_retry_api_returns_initial_fetch_task(tmp_path) -> None:
    source = _stored(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    legacy = _legacy(source)
    repo.enqueue(legacy)
    repo.update_status(legacy.task_id, TaskStatus.FAILED)
    client = TestClient(create_app(tmp_path))

    response = client.post(f"/api/tasks/{legacy.task_id}/retry")

    assert response.status_code == 200
    body = response.json()
    assert body["redirected"] is True
    assert body["redirected_from"] == legacy.task_id
    assert body["task"]["task_type"] == "fetch"
    assert body["task"]["artifacts_summary"]["initial_fetch"] is True
