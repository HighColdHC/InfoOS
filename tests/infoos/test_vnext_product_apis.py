from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.application.tasks.batches import create_task_batch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from tests.infoos._legacy_history import seed_legacy_card_products
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository, SourceRepository
from tests.infoos._factories import make_x_source_payload


def _seed_source_card(tmp_path) -> None:
    SourceRepository(tmp_path).upsert_source(
        make_x_source_payload(source_id="x_validation", identifier="elonmusk", display_name="Elon Musk")
    )
    CardRepository(tmp_path).upsert_card(
        {
            "card_id": "card_source_feed_1",
            "source_id": "x_validation",
            "source_platform": "x",
            "source_type": "social_post",
            "source_url": "https://x.com/elonmusk/status/1",
            "source_author": "Elon Musk",
            "title": "AI product workflow pain",
            "content": "AI builders complain about workflow pain and will pay for better automation.",
            "summary": "Repeated workflow pain creates a business opportunity.",
            "primary_domain_id": None,
            "secondary_domain_ids": [],
            "published_at": "2026-06-24T01:00:00Z",
            "captured_at": "2026-06-24T01:01:00Z",
            "processed_at": "2026-06-24T01:02:00Z",
            "workflow_status": "routed",
            "raw_payload": {"raw_item_id": "tweet_1", "scores": {"creation_score": 5, "insight_score": 5}},
        }
    )
    CardContentBlockRepository(tmp_path).upsert_block(
        {
            "block_id": "block_card_source_feed_1_original",
            "card_id": "card_source_feed_1",
            "position": 0,
            "layer": "original",
            "block_kind": "original_tweet",
            "status": "preserved",
            "body": "AI builders complain about workflow pain and will pay for better automation.",
            "segments": [],
            "source_url": "https://x.com/elonmusk/status/1",
            "provenance": "X original",
            "raw_payload": {"view_count": 1000},
        }
    )
    seed_legacy_card_products(tmp_path, "card_source_feed_1")


def test_task_batch_api_lists_batch_and_children(tmp_path):
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(
        Task(
            task_id="fetch_source_a",
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="source_a",
        )
    )
    batch = create_task_batch(
        tmp_path,
        batch_type="source_run",
        task_ids=["fetch_source_a"],
        source_ids=["source_a"],
    )
    client = TestClient(create_app(tmp_path))

    response = client.get(f"/api/task-batches/{batch['batch_id']}")

    assert response.status_code == 200
    payload = response.json()["batch"]
    assert payload["batch_id"] == batch["batch_id"]
    assert payload["tasks"][0]["task_id"] == "fetch_source_a"
    assert payload["summary"]["planned"] == 1


def test_source_run_route_returns_batch_id(tmp_path):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "source_a",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Source A",
            "cadence_hours": 12,
            "enabled": True,
            "connector_status": "supported",
        }
    )
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/source-run-batches", json={"source_ids": ["source_a"], "limit": 10})

    assert response.status_code == 202
    payload = response.json()["batch"]
    assert payload["batch_id"].startswith("source_run_")
    assert payload["status"] == "queued"
    assert payload["task_ids"]
    assert payload["status_url"] == f"/api/task-batches/{payload['batch_id']}"


def test_source_run_batch_api_does_not_reuse_due_pending_fetch_tasks(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_validation",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Validation RSS",
            "cadence_hours": 1.0,
            "enabled": True,
            "connector_status": "supported",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    due_fetch_id = "fetch_rss_validation_20260625_000000"
    repo.enqueue(
        Task(
            task_id=due_fetch_id,
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss_validation",
            next_run_at="2026-06-25T00:00:00Z",
        )
    )
    monkeypatch.setattr("backend.application.workflows.source_batch.now_utc_iso", lambda: "2026-06-25T00:01:00Z")
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/source-run-batches",
        json={"source_ids": ["rss_validation"], "limit": 10, "max_workers": 2},
    )

    assert response.status_code == 202
    payload = response.json()["batch"]
    assert payload["status"] == "queued"
    assert payload["task_ids"] != [due_fetch_id]
    assert payload["metadata"]["planned_task_ids"] == payload["task_ids"]


def test_source_run_batch_api_uses_parallel_task_worker(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        make_x_source_payload(source_id="x_validation", identifier="elonmusk", display_name="Elon Musk")
    )
    captured: dict[str, object] = {}

    def fake_plan(base_dir, *, now, horizon_hours, plan_followup_fetch, force_now):
        captured["plan_base_dir"] = base_dir
        captured["plan_now"] = now
        captured["horizon_hours"] = horizon_hours
        captured["plan_followup_fetch"] = plan_followup_fetch
        captured["force_now"] = force_now
        return [
            Task(
                task_id="fetch_x_validation_1",
                task_type=TaskType.FETCH,
                target_kind="source",
                target_id="x_validation",
            )
        ]

    monkeypatch.setattr("backend.application.workflows.source_batch.now_utc_iso", lambda: "2026-06-25T00:00:00Z")
    monkeypatch.setattr("backend.application.workflows.source_batch.plan_fetch_tasks_for_enabled_sources", fake_plan)
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/source-run-batches", json={"limit": 40, "max_workers": 6})

    assert response.status_code == 202
    payload = response.json()
    assert payload["ok"] is True
    assert payload["batch"]["metadata"]["source_count"] == 1
    assert payload["batch"]["metadata"]["planned_task_count"] == 1
    assert payload["batch"]["task_ids"] == ["fetch_x_validation_1"]
    assert payload["batch"]["progress_url"].startswith("/api/task-batches/")
    assert captured["horizon_hours"] == 0
    assert captured["plan_followup_fetch"] is False
    assert captured["force_now"] is True


def test_source_run_batch_selected_sources_plan_only_immediate_tasks(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_fast",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Fast RSS",
            "cadence_hours": 0.5,
            "enabled": True,
            "connector_status": "supported",
        }
    )
    monkeypatch.setattr("backend.application.workflows.source_batch.now_utc_iso", lambda: "2026-06-25T00:00:00Z")
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/source-run-batches",
        json={"source_ids": ["rss_fast"], "limit": 10, "max_workers": 2},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["batch"]["metadata"]["planned_task_count"] == 1
    planned_task_ids = payload["batch"]["metadata"]["planned_task_ids"]
    assert len(planned_task_ids) == 1
    assert planned_task_ids[0].startswith("fetch_rss_fast_20260625_000000_")
    assert "_manual_" in planned_task_ids[0]
    assert payload["batch"]["task_ids"] == planned_task_ids


def test_source_run_batch_selected_sources_force_run_even_when_next_run_is_future(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_future",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Future RSS",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "next_run_at": "2026-06-26T00:00:00Z",
        }
    )
    monkeypatch.setattr("backend.application.workflows.source_batch.now_utc_iso", lambda: "2026-06-25T00:00:00Z")
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/source-run-batches", json={"source_ids": ["rss_future"]})

    assert response.status_code == 202
    planned_task_ids = response.json()["batch"]["metadata"]["planned_task_ids"]
    assert len(planned_task_ids) == 1
    assert planned_task_ids[0].startswith("fetch_rss_future_20260625_000000_")
    assert "_manual_" in planned_task_ids[0]


def test_source_run_batch_selected_sources_creates_a_fresh_manual_chain(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        {
            "source_id": "rss_manual",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/feed.xml",
            "display_name": "Manual RSS",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
        }
    )
    repo = TaskRepository.for_base_dir(tmp_path)
    old_due_task_id = "fetch_rss_manual_20260624_000000"
    repo.enqueue(
        Task(
            task_id=old_due_task_id,
            task_type=TaskType.FETCH,
            target_kind="source",
            target_id="rss_manual",
            next_run_at="2026-06-24T00:00:00Z",
            artifacts_summary={"chain_id": "source_run_rss_manual_20260624_000000"},
        )
    )
    monkeypatch.setattr("backend.application.workflows.source_batch.now_utc_iso", lambda: "2026-06-25T00:00:00Z")
    client = TestClient(create_app(tmp_path))

    first = client.post("/api/source-run-batches", json={"source_ids": ["rss_manual"]}).json()["batch"]
    second = client.post("/api/source-run-batches", json={"source_ids": ["rss_manual"]}).json()["batch"]

    assert first["task_ids"] != [old_due_task_id]
    assert second["task_ids"] != [old_due_task_id]
    assert first["task_ids"] != second["task_ids"]
    first_task = repo.get(first["task_ids"][0])
    second_task = repo.get(second["task_ids"][0])
    assert first_task is not None and second_task is not None
    assert first_task.artifacts_summary["chain_id"] != second_task.artifacts_summary["chain_id"]
    assert first_task.artifacts_summary["manual_run"] is True
    assert first_task.artifacts_summary["enforce_source_capability"] is True
    assert repo.get(old_due_task_id).status == TaskStatus.PENDING


def test_source_run_batch_empty_source_ids_does_not_plan_all_sources(tmp_path, monkeypatch):
    SourceRepository(tmp_path).upsert_source(
        make_x_source_payload(source_id="x_validation", identifier="elonmusk", display_name="Elon Musk")
    )
    planned_all = []

    def fail_plan_all(base_dir, *, now):
        planned_all.append(True)
        return ["fetch_should_not_exist"]

    monkeypatch.setattr("backend.application.workflows.source_batch.now_utc_iso", lambda: "2026-06-25T00:00:00Z")
    monkeypatch.setattr("backend.application.workflows.source_batch.plan_fetch_tasks_for_enabled_sources", fail_plan_all)
    client = TestClient(create_app(tmp_path))

    response = client.post("/api/source-run-batches", json={"source_ids": [], "limit": 1, "max_workers": 1})

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "task batch requires at least one root task"}
    assert planned_all == []
