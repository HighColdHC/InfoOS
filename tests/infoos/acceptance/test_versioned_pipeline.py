"""SQLite acceptance coverage for the versioned card-processing DAG."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.infoos._factories import cards_for_source
from backend.application.tasks.batches import create_task_batch, get_task_batch
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_due_tasks
from backend.application.workflows.pipeline_reconciliation import reconcile_pipeline
from backend.domain.models.raw_item import RawItem
from backend.infrastructure.connectors.base import FetchResult
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository, SourceRepository


class PipelineConnector:
    connector_id = "versioned_pipeline_connector"

    def __init__(self, captured_at: str):
        self.captured_at = captured_at

    def fetch_new_items(self, since_cursor: str | None) -> FetchResult:
        assert since_cursor is None
        return FetchResult(items=[_raw_item(self.captured_at)], next_cursor="pipeline-cursor-1", visible_count=1)


def _raw_item(captured_at: str) -> RawItem:
    return RawItem(
        raw_item_id="versioned-pipeline-remote-1",
        connector_id="versioned_pipeline_connector",
        source_platform="rss",
        source_type="article",
        source_url="https://example.com/versioned-pipeline-remote-1",
        captured_at=captured_at,
        raw_text="AI builders need durable workflows that turn source evidence into useful decisions.",
        raw_metadata={
            "source_id": "versioned_pipeline_source",
            "title": "Durable workflow evidence",
        },
    )


def _seed_source(base_dir: Path) -> Task:
    SourceRepository(base_dir).upsert_source(
        {
            "source_id": "versioned_pipeline_source",
            "platform": "rss",
            "source_type": "feed",
            "identifier": "https://example.com/versioned-pipeline.xml",
            "display_name": "Versioned pipeline acceptance",
            "cadence_hours": 24,
            "enabled": True,
            "connector_status": "supported",
            "connector_id": "versioned_pipeline_connector",
        }
    )
    return Task(
        task_id="fetch_versioned_pipeline_source",
        task_type=TaskType.FETCH,
        target_kind="source",
        target_id="versioned_pipeline_source",
        artifacts_summary={
            "run_id": "run-versioned-pipeline",
            "chain_id": "chain-versioned-pipeline",
            "chain_root_task_id": "fetch_versioned_pipeline_source",
        },
    )


def _run_until_idle(base_dir: Path) -> None:
    for _ in range(20):
        result = run_due_tasks(base_dir, max_workers=1, limit=30)
        if result["processed"] == 0:
            return
    raise AssertionError("versioned pipeline did not reach an idle durable queue")


def _task_counts(base_dir: Path) -> dict[TaskType, int]:
    repo = TaskRepository.for_base_dir(base_dir)
    return {task_type: len(repo.list_tasks(task_type=task_type, limit=100)) for task_type in TaskType}


@pytest.fixture
def completed_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    from backend.application.enrichment import generic_blocks
    from backend.application.workflows import acquisition

    # The source boundary is fake; every task and product below
    # is still created by production handlers in the temporary SQLite database.
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(acquisition, "_build_connector", lambda *_args: PipelineConnector(captured_at))
    monkeypatch.setattr(generic_blocks, "enrich_generic_card_blocks", lambda *_args: [])

    root = _seed_source(tmp_path)
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(root)
    batch = create_task_batch(tmp_path, batch_type="acceptance", task_ids=[root.task_id])
    _run_until_idle(tmp_path)

    card = cards_for_source(tmp_path, 'versioned_pipeline_source')[0]
    return {
        "base_dir": tmp_path,
        "card_id": str(card["card_id"]),
        "batch_id": str(batch["batch_id"]),
        "counts": _task_counts(tmp_path),
    }


def _stage_tasks(base_dir: Path, card_id: str) -> dict[TaskType, list[Task]]:
    repo = TaskRepository.for_base_dir(base_dir)
    return {
        task_type: repo.list_by_target(target_kind="card", target_id=card_id, task_type=task_type)
        for task_type in (
            TaskType.CONTENT_ENRICH,
            TaskType.MEDIA_DOWNLOAD,
            TaskType.CARD_FINALIZE,
        )
    }


def test_source_chain_reaches_finalized_information_card_without_second_scheduler_cycle(
    completed_pipeline: dict[str, object],
) -> None:
    base_dir = completed_pipeline["base_dir"]
    card_id = completed_pipeline["card_id"]
    assert isinstance(base_dir, Path)
    assert isinstance(card_id, str)

    stages = _stage_tasks(base_dir, card_id)
    assert all(len(tasks) == 1 for tasks in stages.values())
    assert all(tasks[0].status in {TaskStatus.SUCCESS, TaskStatus.SKIPPED} for tasks in stages.values())
    assert stages[TaskType.CARD_FINALIZE][0].status == TaskStatus.SUCCESS
    card = CardRepository(base_dir).get_card(card_id)
    assert card is not None
    assert card["status"] == "active"
    assert card["version"] == 1
    assert str(card["content_hash"]).startswith("sha256:")

    repo = TaskRepository.for_base_dir(base_dir)
    for legacy_type in (
        TaskType.CONTEXT_PACK,
        TaskType.SUMMARY,
        TaskType.SEMANTIC_EXTRACT,
        TaskType.SCORE,
        TaskType.DECIDE,
        TaskType.OUTPUT_GENERATE,
        TaskType.TOPIC_UPDATE,
        TaskType.CLUSTER,
    ):
        assert repo.list_tasks(task_type=legacy_type, limit=100) == []


def test_same_input_replay_executes_each_stage_once(completed_pipeline: dict[str, object]) -> None:
    base_dir = completed_pipeline["base_dir"]
    assert isinstance(base_dir, Path)
    before = _task_counts(base_dir)

    result = reconcile_pipeline(base_dir)
    _run_until_idle(base_dir)

    assert result["created_by_type"] == {}
    assert _task_counts(base_dir) == before


def test_deliverable_content_change_creates_one_new_finalize_version(
    completed_pipeline: dict[str, object],
) -> None:
    base_dir = completed_pipeline["base_dir"]
    card_id = completed_pipeline["card_id"]
    assert isinstance(base_dir, Path)
    assert isinstance(card_id, str)
    before = CardRepository(base_dir).get_card(card_id)
    assert before is not None
    block_repo = CardContentBlockRepository(base_dir)
    block_repo.upsert_block(
        {
            "block_id": f"body_{card_id}",
            "card_id": card_id,
            "position": 0,
            "layer": "source",
            "block_kind": "article_body",
            "status": "enriched",
            "body": "New durable source evidence.",
            "segments": [],
            "provenance": "source",
            "raw_payload": {},
        }
    )

    result = reconcile_pipeline(base_dir)
    _run_until_idle(base_dir)
    after = CardRepository(base_dir).get_card(card_id)

    assert result["created_by_type"] == {TaskType.CARD_FINALIZE.value: 1}
    assert after is not None
    assert after["version"] == before["version"] + 1
    assert after["content_hash"] != before["content_hash"]


def test_batch_contains_all_descendants(completed_pipeline: dict[str, object]) -> None:
    base_dir = completed_pipeline["base_dir"]
    batch_id = completed_pipeline["batch_id"]
    assert isinstance(base_dir, Path)
    assert isinstance(batch_id, str)

    batch = get_task_batch(base_dir, batch_id)
    repo = TaskRepository.for_base_dir(base_dir)
    task_types = {repo.get(task_id).task_type for task_id in batch["task_ids"] if repo.get(task_id) is not None}

    # Evaluation remains queued until an interest profile and binding exist.
    assert batch["status"] == "success"
    assert task_types == {
        TaskType.FETCH,
        TaskType.CARD_BUILD,
        TaskType.CONTENT_ENRICH,
        TaskType.MEDIA_DOWNLOAD,
        TaskType.CARD_FINALIZE,
    }
