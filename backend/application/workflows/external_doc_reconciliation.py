"""Plan bounded retries for container-browser external document acquisition."""
from __future__ import annotations

from collections import Counter
from typing import Any

from backend.application.tasks.input_versions import InputSnapshot, enrichment_input_version
from backend.application.tasks.planner import versioned_task_id
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.tasks import is_task_cancelled

EXTERNAL_DOC_RETRY_POLICY_VERSION = "external-doc-retry-v3-policy-scoped"
MAX_EXTERNAL_DOC_RETRIES = 6
_AUTHENTICATED_MEDIA_RETRY_REASON = "external_doc_authenticated_media_unavailable"
_DIRECT_MEDIA_GAP_REASON = "direct_media_download_failed"

_RETRYABLE_REASONS = {
    "external_doc_browser_unavailable",
    "external_doc_browser_lock_timeout",
    "external_doc_browser_open_failed",
    "external_doc_browser_target_missing",
    "external_doc_browser_eval_failed",
    "external_doc_browser_invalid_output",
    "external_doc_browser_exception",
    "external_doc_adapter_failed",
    "external_doc_login_required",
    "external_doc_visible_body_unavailable",
}


def reconcile_external_doc_retry(
    repo: TaskRepository,
    card: dict[str, Any],
    blocks: list[dict[str, Any]],
    created: Counter[str],
) -> None:
    if card["source_platform"] not in {"scys", "zsxq"}:
        return
    block = next(
        (item for item in blocks if item["block_kind"] == "external_doc"),
        None,
    )
    payload = block.get("raw_payload") if isinstance((block or {}).get("raw_payload"), dict) else {}
    reason = _retry_reason(block, payload, blocks)
    if not reason:
        return
    card_id = str(card.get("card_id") or "")
    policy_tasks = [
        task
        for task in repo.list_by_target(
            target_kind="card",
            target_id=card_id,
            task_type=TaskType.CONTENT_ENRICH,
        )
        if task.artifacts_summary.get("external_doc_retry_policy_version")
        == EXTERNAL_DOC_RETRY_POLICY_VERSION
    ]
    if len(policy_tasks) >= MAX_EXTERNAL_DOC_RETRIES or any(
        is_task_cancelled(task) or task.status
        not in {
            TaskStatus.SUCCESS,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.SKIPPED,
        }
        for task in policy_tasks
    ):
        return
    retry_number = len(policy_tasks) + 1
    input_version = InputSnapshot(
        target_kind="card",
        target_id=card_id,
        stage="content_enrich_external_doc_retry",
        payload={
            "base_enrichment_version": enrichment_input_version(card),
            "policy_version": EXTERNAL_DOC_RETRY_POLICY_VERSION,
            "retry_number": retry_number,
            "document_url": payload.get("document_url"),
        },
    ).version()
    task = Task(
        task_id=versioned_task_id(TaskType.CONTENT_ENRICH, card_id, input_version),
        task_type=TaskType.CONTENT_ENRICH,
        target_kind="card",
        target_id=card_id,
        input_version=input_version,
        artifacts_summary={
            "card_id": card_id,
            "external_doc_retry": True,
            "external_doc_retry_number": retry_number,
            "external_doc_retry_reason": reason,
            "external_doc_retry_policy_version": EXTERNAL_DOC_RETRY_POLICY_VERSION,
            "reconciled": True,
        },
    )
    if repo.get(task.task_id) is not None:
        return
    repo.enqueue(task)
    created[TaskType.CONTENT_ENRICH.value] += 1


def _retry_reason(
    block: dict[str, Any] | None,
    payload: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> str | None:
    block_reason = str(payload.get("block_reason") or "")
    if block_reason in _RETRYABLE_REASONS:
        return block_reason
    if block is None or block["status"] != "body_fetched":
        return None
    if str(payload.get("provider") or "") != "feishu":
        return None
    media_urls = [
        str(url)
        for key in ("images", "videos")
        for url in (payload.get(key) or [])
        if str(url).strip()
    ]
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    authenticated_count = int(diagnostics.get("authenticated_media_count") or 0)
    has_direct_media_gap = any(
        item["block_kind"] == "media_download_gap"
        and item["status"] == "blocked"
        and _block_reason(item) == _DIRECT_MEDIA_GAP_REASON
        for item in blocks
    )
    if media_urls and authenticated_count == 0 and has_direct_media_gap:
        return _AUTHENTICATED_MEDIA_RETRY_REASON
    return None


def _block_reason(block: dict[str, Any]) -> str:
    payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
    return str(payload.get("reason") or payload.get("block_reason") or "")
