from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.application.tasks.batches import attach_task_to_parent_batches
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.api.routes.research_runs import router
from backend.api.routes.source_runs import router as source_runs_router
from backend.api.routes.sources import router as sources_router
from backend.infrastructure.storage.paths import resolve_sqlite_path
from backend.infrastructure.storage.repositories import CardRepository
from backend.read_models.research_runs import read_research_run, read_research_runs


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(router)
    return TestClient(app)


def _source_api_client(tmp_path) -> TestClient:
    app = FastAPI()
    app.state.base_dir = tmp_path
    app.include_router(router)
    app.include_router(sources_router)
    app.include_router(source_runs_router)
    return TestClient(app)


def _payload(name: str = "AI 出海") -> dict:
    return {
        "name": name,
        "keywords": ["AI", "agents"],
        "platforms": [
            {
                "platform": "github",
                "max_items": 10,
                "sort": "latest",
                "options": {},
            }
        ],
    }


def test_research_run_api_create_replay_list_and_detail(tmp_path) -> None:
    client = _client(tmp_path)

    created = client.post(
        "/api/research-runs",
        json=_payload(),
        headers={"Idempotency-Key": "request-1"},
    )
    assert created.status_code == 202
    created_payload = created.json()
    assert created_payload["ok"] is True
    assert created_payload["replayed"] is False
    run_id = created_payload["research_run"]["research_run_id"]

    replay = client.post(
        "/api/research-runs",
        json=_payload(),
        headers={"Idempotency-Key": "request-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    listed = client.get("/api/research-runs")
    assert listed.status_code == 200
    assert listed.json()["research_runs"][0]["research_run_id"] == run_id

    detail = client.get(f"/api/research-runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["platform_runs"][0]["platform"] == "github"
    assert detail.json()["item_page"] == {"items": [], "next_cursor": None}


def test_research_run_api_returns_stable_idempotency_error(tmp_path) -> None:
    client = _client(tmp_path)
    client.post(
        "/api/research-runs",
        json=_payload(),
        headers={"Idempotency-Key": "request-1"},
    )

    conflict = client.post(
        "/api/research-runs",
        json=_payload("Changed"),
        headers={"Idempotency-Key": "request-1"},
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


@pytest.mark.no_storage_bootstrap
def test_research_read_models_do_not_create_a_database(tmp_path) -> None:
    assert read_research_runs(tmp_path) == []
    assert read_research_run(tmp_path, "missing") is None
    assert not resolve_sqlite_path(tmp_path).exists()


def test_detail_uses_durable_batch_card_membership_and_counts_only_root_platforms(
    tmp_path,
) -> None:
    client = _client(tmp_path)
    first_id = client.post(
        "/api/research-runs",
        json=_payload("First"),
        headers={"Idempotency-Key": "request-1"},
    ).json()["research_run"]["research_run_id"]
    second_id = client.post(
        "/api/research-runs",
        json=_payload("Second"),
        headers={"Idempotency-Key": "request-2"},
    ).json()["research_run"]["research_run_id"]

    db_path = resolve_sqlite_path(tmp_path)
    repo = TaskRepository(str(db_path))
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_shared",
            "source_platform": "github",
            "source_type": "repository",
            "source_url": "https://github.com/openai/example",
            "title": "Shared",
            "content": "Body",
            "status": "active",
            "completeness_status": "complete",
            "captured_at": "2026-07-31T00:00:00Z",
            "processed_at": "2026-07-31T00:00:00Z",
            "published_at": "2026-07-30T00:00:00Z",
        }
    )
    for index, batch_id in enumerate((first_id, second_id), start=1):
        with repo._connect() as conn:
            root_id = conn.execute(
                "select task_id from task_batch_items where batch_id = ? order by rowid limit 1",
                (batch_id,),
            ).fetchone()["task_id"]
            conn.execute(
                "update tasks set status = 'success' where task_id = ?",
                (root_id,),
            )
        child = Task(
            task_id=f"card_build_shared_{index}",
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id=f"research:{batch_id}:github::shared",
            status=TaskStatus.SUCCESS,
            artifacts_summary={"card_id": "card_shared"},
        )
        repo.enqueue(child)
        attach_task_to_parent_batches(
            tmp_path,
            parent_task_id=root_id,
            child_task_ids=[child.task_id],
        )

    first = read_research_run(tmp_path, first_id)
    second = read_research_run(tmp_path, second_id)

    assert first["item_page"]["items"][0]["card_id"] == "card_shared"
    assert second["item_page"]["items"][0]["card_id"] == "card_shared"
    assert first["research_run"]["completed_platform_count"] == 1
    assert first["research_run"]["failed_platform_count"] == 0


def test_hidden_research_source_is_rejected_by_ordinary_source_mutations_and_runs(
    tmp_path,
) -> None:
    client = _source_api_client(tmp_path)
    run_id = client.post(
        "/api/research-runs",
        json=_payload(),
        headers={"Idempotency-Key": "request-1"},
    ).json()["research_run"]["research_run_id"]
    source_id = f"{run_id}_github"

    assert client.get(f"/api/configurable-sources/{source_id}").status_code == 404
    assert client.patch(
        f"/api/configurable-sources/{source_id}/enabled",
        json={"enabled": False},
    ).status_code == 404
    assert client.delete(f"/api/configurable-sources/{source_id}").status_code == 404
    assert client.post(f"/api/configurable-sources/{source_id}/run").status_code == 404
