from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from backend.infrastructure.storage.task_batch_queries import TaskBatch, get_task_batch, list_task_batches
from backend.infrastructure.storage.task_queries import TaskReadQuery
from backend.read_models.task_manager import TaskItem, _task_to_dict
from backend.read_models.task_manager_context import card_context_by_id, source_by_card_id


class TaskBatchDetail(TaskBatch):
    tasks: list[TaskItem]


def read_task_batch(base_dir: Path | str, batch_id: str) -> TaskBatchDetail:
    batch = get_task_batch(base_dir, batch_id)
    base_path = Path(base_dir)
    tasks = TaskReadQuery(base_path).get_many(batch["task_ids"])
    source_by_card = source_by_card_id(base_path, tasks)
    card_contexts = card_context_by_id(base_path, tasks)
    current = datetime.now(timezone.utc)
    return {
        **batch,
        "tasks": [
            _task_to_dict(
                task,
                base_dir=base_path,
                source_by_card=source_by_card,
                card_context_by_id=card_contexts,
                current=current,
            )
            for task in tasks
        ],
    }


def read_task_batches(base_dir: Path | str, *, limit: int = 100) -> list[TaskBatch]:
    return list_task_batches(base_dir, limit=limit)
