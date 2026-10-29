"""Card lookup and task routes read by key or in SQL instead of scanning a window."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import (
    CardRepository,
    SourceRepository,
)
from backend.infrastructure.storage.task_queries import TaskReadQuery
from backend.read_models.source_run_trace_safety import task_identifier_alias
from tests.infoos._factories import make_supported_source_payload


def _card(card_id: str, **overrides) -> dict:
    return {
        "card_id": card_id,
        "source_id": overrides.pop("source_id", None),
        "source_platform": "rss",
        "source_type": "article",
        "source_url": f"https://example.com/{card_id}",
        "title": f"Title {card_id}",
        "content": "Body",
        "summary": "Body",
        "captured_at": "2026-09-01T00:00:00Z",
        "processed_at": "2026-09-01T00:00:01Z",
        "raw_payload": {},
        **overrides,
    }


def _insert_tasks(base_dir: Path, rows: list[tuple[str, str, str, str, str, str]]) -> None:
    """Bulk rows of (task_id, task_type, target_kind, target_id, status, created_at)."""
    with sqlite3.connect(resolve_sqlite_path(base_dir)) as conn:
        conn.executemany(
            "insert into tasks(task_id, task_type, target_kind, target_id, status, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?)",
            [(*row, row[5]) for row in rows],
        )


def _newer_tasks(count: int) -> list[tuple[str, str, str, str, str, str]]:
    return [
        (f"fetch_new_{index:05d}", "fetch", "source", "src_new", "success", f"2026-09-02T00:00:{index % 60:02d}Z")
        for index in range(count)
    ]


def _no_full_card_scan(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("card detail must not load the card table")

    monkeypatch.setattr(CardRepository, "list_cards", fail, raising=False)


def test_task_filters_reach_tasks_older_than_the_newest_thousand(tmp_path):
    SourceRepository(tmp_path).upsert_source(make_supported_source_payload(source_id="src_old"))
    CardRepository(tmp_path).upsert_card(_card("card_old", source_id="src_old"))
    TaskRepository.for_base_dir(tmp_path)
    _insert_tasks(tmp_path, [
        ("fetch_old", "fetch", "source", "src_old", "failed", "2020-01-01T00:00:00Z"),
        ("summary_old", "summary", "card", "card_old", "success", "2020-01-01T00:00:01Z"),
        *_newer_tasks(1_005),
    ])
    with sqlite3.connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute(
            "update tasks set artifacts_summary = ?, last_failure_class = ?, started_at = ? where task_id = ?",
            ('{"chain_id": "chain_old"}', "network", "2020-01-01T00:00:05Z", "fetch_old"),
        )
    client = TestClient(create_app(tmp_path))

    def ids(**params) -> list[str]:
        response = client.get("/api/tasks", params=params)
        assert response.status_code == 200
        return [row["task_id"] for row in response.json()["tasks"]]

    assert ids(target_id="src_old") == ["fetch_old"]
    assert ids(chain_id="chain_old") == ["fetch_old"]
    assert ids(failure_class="network", status="failed") == ["fetch_old"]
    assert ids(source_id="src_old") == ["summary_old", "fetch_old"]
    assert ids(started_from="2020-01-01T00:00:00Z", started_to="2020-01-02T00:00:00Z") == ["fetch_old"]
    assert ids(task_type="summary") == ["summary_old"]
    assert len(ids(target_kind="source", limit=5000)) == 1_000
    assert ids(target_kind="source", limit=0) == ["fetch_new_00059"]
    row = client.get("/api/tasks", params={"target_id": "src_old"}).json()["tasks"][0]
    assert row["chain_id"] == "chain_old"
    assert row["source_id"] == "src_old"


def test_task_list_rejects_unknown_status_and_type(tmp_path):
    client = TestClient(create_app(tmp_path))

    assert client.get("/api/tasks", params={"status": "nope"}).status_code == 400
    assert client.get("/api/tasks", params={"task_type": "nope"}).status_code == 400


def test_retry_resolves_an_alias_beyond_ten_thousand_tasks(tmp_path):
    TaskRepository.for_base_dir(tmp_path).enqueue(
        Task(task_id="fetch_old_failed", task_type=TaskType.FETCH, target_kind="source", target_id="src_old")
    )
    TaskRepository.for_base_dir(tmp_path).update_status("fetch_old_failed", TaskStatus.FAILED, error_reason="timeout")
    with sqlite3.connect(resolve_sqlite_path(tmp_path)) as conn:
        conn.execute("update tasks set created_at = ? where task_id = ?", ("2020-01-01T00:00:00Z", "fetch_old_failed"))
    _insert_tasks(tmp_path, _newer_tasks(10_001))
    client = TestClient(create_app(tmp_path))

    retried = client.post(f"/api/tasks/{task_identifier_alias('fetch_old_failed')}/retry")

    assert retried.status_code == 200
    assert retried.json()["task"]["task_id"] == "fetch_old_failed"
    assert retried.json()["task"]["status"] == "pending"


def test_retry_alias_of_a_finished_task_is_not_retryable(tmp_path):
    TaskRepository.for_base_dir(tmp_path)
    _insert_tasks(tmp_path, _newer_tasks(3))
    client = TestClient(create_app(tmp_path))

    finished = client.post(f"/api/tasks/{task_identifier_alias('fetch_new_00001')}/retry")
    unknown = client.post(f"/api/tasks/{task_identifier_alias('fetch_missing')}/retry")

    assert finished.status_code == 409
    assert finished.json()["code"] == "task_not_retryable"
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "task_not_found"


@pytest.mark.parametrize("public_id", ["not-an-alias", "task_" + "0" * 23, "task_" + "G" * 24])
def test_retry_with_a_malformed_id_does_not_scan_tasks(tmp_path, monkeypatch, public_id):
    TaskRepository.for_base_dir(tmp_path)
    client = TestClient(create_app(tmp_path))

    def fail(*_args, **_kwargs):
        raise AssertionError("malformed ids must not stream task ids")

    monkeypatch.setattr(TaskReadQuery, "iter_task_ids", fail)

    response = client.post(f"/api/tasks/{public_id}/retry")

    assert response.status_code == 404
    assert response.json()["code"] == "task_not_found"
