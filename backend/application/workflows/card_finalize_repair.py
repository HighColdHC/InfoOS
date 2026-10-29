"""Repair terminal card state without duplicating a successful finalize task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus
from backend.application.tasks.types import TaskType
from backend.application.workflows.pipeline_source_lifecycle import card_source_id
from backend.infrastructure.storage.repositories import CardRepository


def repair_card_from_successful_finalize(
    base_dir: Path,
    card: dict[str, Any],
    finalize_task: Task,
) -> bool:
    """Replay an idempotent terminal write when a rebuild reset only card state."""
    if finalize_task.status != TaskStatus.SUCCESS:
        return False
    if card["status"] != "processing":
        return False
    expected_hash = str(finalize_task.artifacts_summary.get("content_hash") or "")
    if not expected_hash or expected_hash != card["content_hash"]:
        return False
    from backend.application.tasks.handlers.card_finalize import handle_card_finalize

    result = handle_card_finalize(finalize_task, base_dir)
    return result["status"] == TaskStatus.SUCCESS.value


def repair_terminal_card_states(
    repo: TaskRepository,
    base_dir: Path,
    sources_by_id: dict[str, dict[str, Any]],
    *,
    limit: int,
) -> int:
    """Repair proven terminal cards ahead of the ordinary keyset cursor."""
    repaired = 0
    candidates = CardRepository(base_dir).list_terminal_state_repair_candidates(
        limit=limit
    )
    for card in candidates:
        source_id = card_source_id(card)
        if source_id and source_id not in sources_by_id:
            continue
        finalize_task = next(
            (
                task
                for task in repo.list_by_target(
                    target_kind="card",
                    target_id=str(card.get("card_id") or ""),
                    task_type=TaskType.CARD_FINALIZE,
                )
                if task.status == TaskStatus.SUCCESS
                and task.artifacts_summary.get("content_hash")
                == card["content_hash"]
            ),
            None,
        )
        if finalize_task is not None and repair_card_from_successful_finalize(
            base_dir,
            card,
            finalize_task,
        ):
            repaired += 1
    return repaired
