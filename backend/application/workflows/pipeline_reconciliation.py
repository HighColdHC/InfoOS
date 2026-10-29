"""Repair missing, current-version work in the durable card pipeline."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

from backend.application.enrichment.external_doc_inputs import content_enrich_queue_artifacts
from backend.application.tasks.input_versions import (
    card_build_input_version,
    enrichment_input_version,
    media_input_version,
)
from backend.application.tasks.planner import (
    now_utc_iso,
    raw_item_target_id,
    raw_item_task_id,
    versioned_task_id,
)
from backend.application.tasks.repository import TaskRepository
from backend.application.tasks.transitions import (
    TERMINAL_STATUSES,
    enqueue_media_download,
    enqueue_media_subtasks,
)
from backend.application.tasks.card_finalize_transitions import (
    enqueue_card_finalize,
    enqueue_card_finalize_after_pipeline_failure,
    enqueue_card_finalize_after_media,
)
from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.tasks import is_task_cancelled
from backend.application.workflows.source_initial_fetch import (
    enqueue_or_reuse_initial_fetch,
    source_needs_initial_work,
)
from backend.application.workflows.pipeline_batch_reconciliation import repair_batch_links
from backend.application.workflows.external_doc_reconciliation import reconcile_external_doc_retry
from backend.infrastructure.storage.repositories import (
    CardContentBlockRepository,
    CardRepository,
    RawItemRepository,
    SourceRepository,
)
from backend.infrastructure.storage.reconciliation_cursors import ReconciliationCursorRepository
from backend.application.workflows.pipeline_source_lifecycle import (
    card_source_id,
    raw_item_source_id,
)
from backend.application.workflows.card_finalize_repair import (
    repair_card_from_successful_finalize,
    repair_terminal_card_states,
)
from backend.application.workflows.pipeline_reconciliation_scan import (
    fair_scan_budgets,
    load_raw_cursor,
)


_INITIAL_FETCH_RECOVERY_KEY = "pipeline-reconciliation-v1"


def reconcile_pipeline(
    base_dir: Path | str,
    *,
    limit: int = 500,
) -> dict[str, Any]:
    """Queue only absent current-version tasks and repair batch descendants.

    The workflow does not retry, overwrite, or delete historical work.  A
    task's versioned identity makes every enqueue idempotent while still
    allowing changed durable inputs to yield another task.
    """
    base_path = Path(base_dir)
    raw_limit, card_limit, batch_limit = fair_scan_budgets(limit)
    repo = TaskRepository.for_base_dir(base_path)
    created = Counter()
    errors: list[str] = []
    scanned = 0

    calibration_sources_scanned = _reconcile_source_calibrations(
        repo,
        base_path,
        created,
        limit=min(max(0, int(limit)), 100),
    )
    sources_by_id = {
        str(source.get("source_id") or ""): source
        for source in SourceRepository(base_path).list_sources()
    }
    terminal_cards_repaired = repair_terminal_card_states(
        repo,
        base_path,
        sources_by_id,
        limit=min(max(0, int(limit)), 25),
    )

    cursor_repo = ReconciliationCursorRepository(base_path)
    raw_cursor = load_raw_cursor(cursor_repo.get("raw_items"))
    raw_items = RawItemRepository(base_path).list_reconciliation_page(
        after_connector_id=raw_cursor[0],
        after_raw_item_id=raw_cursor[1],
        limit=raw_limit,
    )
    for raw_item in raw_items:
        scanned += 1
        source_id = raw_item_source_id(raw_item)
        if source_id and source_id not in sources_by_id:
            continue
        _reconcile_raw_item(repo, raw_item, created)
    if raw_items:
        last_raw = raw_items[-1]
        cursor_repo.set(
            "raw_items",
            json.dumps(
                [last_raw["connector_id"], last_raw["raw_item_id"]],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )

    cards = CardRepository(base_path).list_reconciliation_page(
        after_card_id=cursor_repo.get("cards"),
        limit=card_limit,
    )
    block_repo = CardContentBlockRepository(base_path)
    for card in cards:
        scanned += 1
        source_id = card_source_id(card)
        if source_id and source_id not in sources_by_id:
            continue
        try:
            _reconcile_card(
                repo,
                base_path,
                card,
                block_repo,
                created,
                source=sources_by_id.get(source_id),
            )
        except Exception as exc:  # noqa: BLE001 - Per-card repair failures must be reported in reconciliation errors without aborting independent cards
            errors.append(f"card:{card['card_id']}:{exc}")
    if cards:
        cursor_repo.set("cards", str(cards[-1].get("card_id") or ""))
    repaired, task_scanned, task_errors = repair_batch_links(
        repo, base_path, cursor_repo=cursor_repo, limit=batch_limit
    )
    scanned += task_scanned
    errors.extend(task_errors)
    return {
        "scanned": scanned,
        "calibration_sources_scanned": calibration_sources_scanned,
        "created_by_type": dict(sorted(created.items())),
        "batch_links_repaired": repaired,
        "terminal_cards_repaired": terminal_cards_repaired,
        "provider_recoveries_scheduled": 0,
        "errors": errors,
    }


def _reconcile_source_calibrations(
    repo: TaskRepository,
    base_dir: Path,
    created: Counter[str],
    *,
    limit: int,
) -> int:
    sources = [
        source for source in SourceRepository(base_dir).list_sources(include_disabled=False)
        if source["connector_status"] == "implemented_uncalibrated"
        and source_needs_initial_work(
            repo,
            source,
            recovery_key=_INITIAL_FETCH_RECOVERY_KEY,
        )
    ][: max(0, limit)]
    for source in sources:
        queued = enqueue_or_reuse_initial_fetch(
            base_dir,
            source,
            now=now_utc_iso(),
            reason="pipeline_reconciliation",
            retry_terminal=True,
            recovery_key=_INITIAL_FETCH_RECOVERY_KEY,
        )
        if queued["created"]:
            created[TaskType.FETCH.value] += 1
    return len(sources)


def _reconcile_raw_item(repo: TaskRepository, raw_item: dict[str, Any], created: Counter[str]) -> None:
    connector_id = raw_item["connector_id"]
    raw_item_id = raw_item["raw_item_id"]
    if not connector_id or not raw_item_id:
        return
    payload = raw_item.get("raw_payload") if isinstance(raw_item.get("raw_payload"), dict) else {}
    raw_timestamps = payload.get("raw_timestamps") if isinstance(payload.get("raw_timestamps"), dict) else {}
    raw_metadata = payload.get("raw_metadata") if isinstance(payload.get("raw_metadata"), dict) else {}
    input_version = card_build_input_version(
        {
            "raw_item_id": raw_item_id,
            "source_url": payload.get("source_url") or raw_item.get("source_url"),
            "raw_text": payload.get("raw_text") or raw_item.get("raw_text"),
            "published_at": raw_timestamps.get("published_at") or raw_item.get("published_at"),
            "raw_metadata": raw_metadata,
        }
    )
    task_id = raw_item_task_id(
        TaskType.CARD_BUILD,
        raw_item_id,
        connector_id=connector_id,
        input_version=input_version,
    )
    if repo.get(task_id) is not None:
        return
    repo.enqueue(
        Task(
            task_id=task_id,
            task_type=TaskType.CARD_BUILD,
            target_kind="raw_item",
            target_id=raw_item_target_id(connector_id, raw_item_id),
            input_version=input_version,
            artifacts_summary={"source_id": raw_item.get("source_id"), "reconciled": True},
        )
    )
    created[TaskType.CARD_BUILD.value] += 1


def _reconcile_card(
    repo: TaskRepository,
    base_dir: Path,
    card: dict[str, Any],
    block_repo: CardContentBlockRepository,
    created: Counter[str],
    *,
    source: dict[str, Any] | None,
) -> None:
    card_id = str(card.get("card_id") or "")
    if not card_id:
        return
    blocks = block_repo.list_for_card(card_id)
    enrich_version = enrichment_input_version(card, source=source)
    enrich_task_id = versioned_task_id(TaskType.CONTENT_ENRICH, card_id, enrich_version)
    enrich_task = repo.get(enrich_task_id)
    if enrich_task is not None and is_task_cancelled(enrich_task):
        return
    reconcile_external_doc_retry(repo, card, blocks, created)
    if enrich_task is None:
        if card["source_platform"] == "youtube" and not _youtube_needs_subtitle_reconciliation(
            block_repo.list_for_card(card_id)
        ):
            successful = [
                task
                for task in repo.list_by_target(
                    target_kind="card", target_id=card_id, task_type=TaskType.CONTENT_ENRICH
                )
                if task.status == TaskStatus.SUCCESS
            ]
            if not successful:
                return
            enrich_task = successful[-1]
        else:
            enrich_task = Task(
                task_id=enrich_task_id,
                task_type=TaskType.CONTENT_ENRICH,
                target_kind="card",
                target_id=card_id,
                input_version=enrich_version,
                artifacts_summary={"card_id": card_id, "reconciled": True,
                                   **content_enrich_queue_artifacts(card, base_dir, blocks=blocks)},
            )
            repo.enqueue(enrich_task)
            created[TaskType.CONTENT_ENRICH.value] += 1
            return
    if enrich_task.status in TERMINAL_STATUSES and enrich_task.status != TaskStatus.SUCCESS:
        existing_task_ids = repo.task_ids_for_card(card_id)
        _record_reconciled_children(
            repo,
            enqueue_card_finalize_after_pipeline_failure(
                repo,
                enrich_task,
                enrich_task.artifacts_summary,
                base_dir,
                enrich_task.status,
            ),
            created,
            existing_task_ids,
        )
        return
    if enrich_task.status != TaskStatus.SUCCESS:
        return

    media_task_id = versioned_task_id(
        TaskType.MEDIA_DOWNLOAD,
        card_id,
        media_input_version(card=card, enrichment_version=enrich_task.input_version),
    )
    media_task = repo.get(media_task_id)
    if media_task is None:
        existing_task_ids = repo.task_ids_for_card(card_id)
        _record_reconciled_children(
            repo,
            enqueue_media_download(repo, enrich_task, base_dir),
            created,
            existing_task_ids,
        )
        return
    if media_task.status not in TERMINAL_STATUSES or is_task_cancelled(media_task):
        return

    existing_task_ids = repo.task_ids_for_card(card_id)
    if media_task.status == TaskStatus.SUCCESS:
        child_ids = enqueue_media_subtasks(repo, media_task, media_task.artifacts_summary, base_dir)
        _record_reconciled_children(repo, child_ids, created, existing_task_ids)
        child_tasks = [repo.get(task_id) for task_id in child_ids]
        if any(
            child is not None
            and child.task_type != TaskType.CARD_FINALIZE
            and child.status not in TERMINAL_STATUSES
            for child in child_tasks
        ):
            return
        finalize_children = [
            child
            for child in child_tasks
            if child is not None and child.task_type == TaskType.CARD_FINALIZE
        ]
        if finalize_children:
            for child in finalize_children:
                repair_card_from_successful_finalize(base_dir, card, child)
            return
        child_ids = enqueue_card_finalize(
            repo,
            card_id,
            dependency_task_id=media_task.task_id,
            parent_task=media_task,
            base_dir=base_dir,
        )
    else:
        child_ids = enqueue_card_finalize_after_media(
            repo,
            media_task,
            media_task.artifacts_summary,
            base_dir,
            media_task.status,
        )
    _record_reconciled_children(repo, child_ids, created, existing_task_ids)


def _youtube_needs_subtitle_reconciliation(blocks: list[dict[str, Any]]) -> bool:
    from backend.application.enrichment.transcripts.subtitles import SUBTITLE_POLICY_VERSION

    usable = any(
        block["block_kind"] == "transcript"
        and isinstance(block.get("raw_payload"), dict)
        and block["raw_payload"].get("usable_transcript") is True
        for block in blocks
    )
    if usable:
        return False
    return not any(
        isinstance(block.get("raw_payload"), dict)
        and block["raw_payload"].get("subtitle_policy_version") == SUBTITLE_POLICY_VERSION
        and block["raw_payload"].get("subtitle_attempt_status") in {"usable", "unavailable", "blocked", "failed"}
        for block in blocks
    )


def _record_reconciled_children(
    repo: TaskRepository,
    child_task_ids: list[str],
    created: Counter[str],
    existing_task_ids: set[str],
) -> bool:
    """Count only rows this repair inserted; versioned enqueue remains idempotent."""
    created_any = False
    for task_id in child_task_ids:
        task = repo.get(task_id)
        if task is None or task_id in existing_task_ids:
            continue
        created[task.task_type.value] += 1
        created_any = True
    return created_any
