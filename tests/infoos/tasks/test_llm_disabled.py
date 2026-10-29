"""T1.Q: built-in LLM tasks cannot call providers or hold the card pipeline."""
from pathlib import Path

import pytest

from backend.application.tasks.registry import handler_for
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.transitions import enqueue_media_subtasks
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.application.tasks.worker import run_tasks_by_ids
from backend.infrastructure.storage.repositories import CardAssetRepository, CardRepository


@pytest.mark.parametrize("kind", [TaskType.CARD_LLM_ENRICH, TaskType.IMAGE_UNDERSTAND, TaskType.SUMMARY, TaskType.CONTEXT_PACK])
def test_retired_handlers_skip_without_work(tmp_path, kind):
    task = Task(task_id="disabled", task_type=kind, target_kind="card" if kind == TaskType.CARD_LLM_ENRICH else "asset", target_id="missing")
    result = handler_for(kind)(task, tmp_path)
    assert result["status"] == "skipped"
    assert result["artifacts"]["reason"] == "feature_retired"


def _card(tmp_path):
    cards = CardRepository(tmp_path)
    cards.upsert_card({"card_id": "card", "source_id": None, "source_platform": "web",
                       "source_type": "capture", "source_url": "https://example.com/card",
                       "title": "Card", "content": "Body", "captured_at": "2026-09-30T00:00:00Z",
                       "processed_at": "2026-09-30T00:00:00Z", "raw_payload": {}})
    cards.finalize_card("card", content_hash="sha256:" + "a" * 64,
                        completeness_status="complete", missing_reasons=[])
    return cards.get_card("card")


def test_downloaded_image_ignores_blocked_understanding_and_finalizes(tmp_path):
    _card(tmp_path)
    assets = CardAssetRepository(tmp_path)
    assets.upsert_asset({"asset_id": "image", "card_id": "card", "asset_kind": "image",
                         "source_url": "https://example.com/image", "status": "downloaded",
                         "local_path": "card/image.jpg"})
    repo = TaskRepository.for_base_dir(tmp_path)
    repo.enqueue(Task(task_id="old-image", task_type=TaskType.IMAGE_UNDERSTAND,
                      target_kind="asset", target_id="image", status=TaskStatus.BLOCKED))
    media = Task(task_id="media", task_type=TaskType.MEDIA_DOWNLOAD, target_kind="card",
                 target_id="card", status=TaskStatus.SUCCESS)
    repo.enqueue(media)
    children = enqueue_media_subtasks(repo, media, {}, tmp_path)
    assert len(children) == 1
    assert repo.get(children[0]).task_type == TaskType.CARD_FINALIZE
    assert run_tasks_by_ids(tmp_path, children)["succeeded"] == 1
    assert CardRepository(tmp_path).get_card("card")["completeness_status"] == "complete"
    assert repo.get("old-image").status == TaskStatus.BLOCKED
