from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.batches import get_task_batch
from backend.application.usecases.task_retry import retry_task


def retry_task_batch(base_dir: Path | str, batch_id: str) -> dict[str, Any]:
    """Retry every retryable task in one existing batch."""
    batch = get_task_batch(base_dir, batch_id)
    retried_task_ids = []
    for task_id in batch["task_ids"]:
        result = retry_task(base_dir, task_id)
        if result["ok"]:
            retried_task_ids.append(str(result["task_id"]))
    return {
        **get_task_batch(base_dir, batch_id),
        "retried_task_ids": retried_task_ids,
    }
