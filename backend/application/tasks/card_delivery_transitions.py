"""Plan token Webhook work from stable card evidence."""
from __future__ import annotations

from typing import Any

from backend.domain.backfill import is_backfill
from backend.application.tasks.finalization import TaskFinalizationQueue
from backend.application.tasks.input_versions import (
    webhook_delivery_input_version,
)
from backend.application.tasks.planner import versioned_task_id
from backend.application.tasks.transitions import child_task_artifacts
from backend.application.tasks.types import DependencySpec, Task, TaskStatus, TaskType


def enqueue_after_card_finalize(
    repo: TaskFinalizationQueue,
    task: Task,
    artifacts: dict[str, Any],
) -> list[str]:
    if (task.artifacts_summary.get("agent_evaluation_finalize")
            or task.artifacts_summary.get("opportunity_breakdown_finalize")
            or task.artifacts_summary.get("verification_finalize")):
        return []
    card = _card(repo, task.target_id)
    if card is None:
        return []
    return enqueue_webhooks(repo, task, card=card)


def enqueue_webhooks(
    repo: TaskFinalizationQueue,
    parent: Task,
    *,
    card: dict[str, Any],
    required_status: TaskStatus = TaskStatus.SUCCESS,
) -> list[str]:
    if card["status"] != "active" or not card["content_hash"]:
        return []
    raw = repo.connection.execute(
        "select first_seen_at from raw_items where connector_id = ? and raw_item_id = ?",
        (card["raw_connector_id"], card["raw_item_id"]),
    ).fetchone()
    # A user capture is an explicit request, whatever the article's age.
    backfill, backfill_reason = (False, None) if parent.artifacts_summary.get("capture_id") else is_backfill(
        card["published_at"], raw["first_seen_at"] if raw else None,
        parent.artifacts_summary.get("initial_fetch") is True
        or str(parent.artifacts_summary.get("chain_id") or "").startswith("source_initial_fetch_"),
    )
    created: list[str] = []
    rows = repo.connection.execute(
        """
        select token_id, webhook_config_generation, webhook_include_backfill from api_tokens
        where webhook_enabled = 1 and webhook_url is not null
          and webhook_delivery_status = 'active'
          and revoked_at is null and replaced_by_token_id is null
        order by token_id
        """
    ).fetchall()
    for row in rows:
        if backfill and not row["webhook_include_backfill"]:
            continue
        token_id = str(row["token_id"])
        input_version = webhook_delivery_input_version(
            token_id=token_id,
            card_id=str(card["card_id"]),
            card_version=int(card["version"]),
            content_hash=str(card["content_hash"]),
        )
        target_identity = f"{token_id}:{card['card_id']}"
        child = Task(
            task_id=versioned_task_id(TaskType.WEBHOOK_DELIVER, target_identity, input_version),
            task_type=TaskType.WEBHOOK_DELIVER,
            target_kind="card",
            target_id=str(card["card_id"]),
            depends_on=(DependencySpec(parent.task_id, required_status),),
            input_version=input_version,
            max_attempts=4,
            artifacts_summary=child_task_artifacts(
                parent,
                {
                    "token_id": token_id,
                    "webhook_config_generation": int(row["webhook_config_generation"]),
                    "card_id": card["card_id"],
                    "source_id": card["source_id"],
                    "card_version": int(card["version"]),
                    "content_hash": card["content_hash"],
                    "finalized_at": card["updated_at"],
                    **({"backfill": backfill, "backfill_reason": backfill_reason}
                       if row["webhook_include_backfill"] else {}),
                },
            ),
        )
        repo.enqueue(child)
        created.append(child.task_id)
    return created


def _card(repo: TaskFinalizationQueue, card_id: str) -> dict[str, Any] | None:
    row = repo.connection.execute("select * from cards where card_id = ?", (card_id,)).fetchone()
    return None if row is None else dict(row)
