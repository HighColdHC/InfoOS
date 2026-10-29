"""Finalize one information card as the collector pipeline's terminal product."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.media_outcomes import is_resolved_x_no_media_outcome


_GAP_BLOCK_KINDS = {"enrichment_gap", "media_download_gap", "external_doc"}
_DELIVERED_ASSET_STATUSES = {
    "available",
    "done",
    "downloaded",
    "enriched",
    "preserved",
    "ready",
    "success",
    "understood",
}
_NON_GAP_MEDIA_REASONS = {"skipped_no_media_expected"}
_OPTIONAL_MISSING_PARTS = {
    "image_understanding",
}
_RETIRED_UPSTREAM_TASK_TYPES = {
    TaskType.IMAGE_UNDERSTAND, TaskType.CARD_LLM_ENRICH,
    TaskType.SUMMARY, TaskType.CONTEXT_PACK,
}


def handle_card_finalize(task: Task, base_dir: Path) -> dict[str, Any]:
    """Publish the current stable card version, retaining all known gaps."""
    from backend.domain.information_cards import information_card_content_hash
    from backend.infrastructure.storage.repositories import (
        CardAssetRepository,
        CardContentBlockRepository,
        CardRepository,
    )

    card_repo = CardRepository(base_dir)
    card = card_repo.get_card(task.target_id)
    if card is None:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"card_id": task.target_id, "reason": "card_missing"},
            "error": "card_missing",
        }

    unfinished_media_task_ids = _unfinished_required_media_task_ids(task, base_dir)
    if unfinished_media_task_ids:
        return {
            "status": TaskStatus.PENDING.value,
            "artifacts": {
                "card_id": task.target_id,
                "reason": "upstream_media_not_terminal",
                "upstream_media_task_ids": unfinished_media_task_ids,
            },
            "error": "upstream_media_not_terminal",
        }

    blocks = CardContentBlockRepository(base_dir).list_for_card(task.target_id)
    assets = CardAssetRepository(base_dir).list_for_card(task.target_id)
    missing_reasons = collect_missing_reasons(
        task,
        blocks=blocks,
        assets=assets,
        base_dir=base_dir,
        source_platform=card["source_platform"],
    )
    from backend.domain.feishu_content import feishu_gap

    source_gap = feishu_gap(card["raw_payload"])
    if source_gap:
        missing_reasons = sorted(set([*missing_reasons, source_gap]))
    from backend.application.enrichment.external_doc_inputs import external_doc_inputs
    from backend.domain.zsxq_article import article_gap

    if card["source_platform"] == "zsxq":
        gap = article_gap(external_doc_inputs(card, base_dir, blocks=blocks)[0])
        if gap:
            missing_reasons = sorted(set([*missing_reasons, gap]))
    completeness_status = "partial" if missing_reasons else "complete"
    content_hash = information_card_content_hash(
        card,
        blocks,
        assets,
        completeness_status=completeness_status,
        missing_reasons=missing_reasons,
    )
    source_run_id = str(
        task.artifacts_summary.get("source_run_id") or card["source_run_id"] or ""
    ).strip() or None
    with card_repo.connect() as conn:
        finalized = card_repo.finalize_card(
            task.target_id,
            content_hash=content_hash,
            completeness_status=completeness_status,
            missing_reasons=missing_reasons,
            processor_version=card["processor_version"],
            source_run_id=source_run_id,
            conn=conn,
        )
    capture_id = str(task.artifacts_summary.get("capture_id") or "")
    if capture_id:
        from backend.infrastructure.storage.base import utc_now
        from backend.infrastructure.storage.capture_receipts import (
            CaptureReceiptRepository,
        )

        CaptureReceiptRepository(base_dir).update_processing(
            capture_id,
            processing_status="succeeded",
            card_id=task.target_id,
            updated_at=utc_now(),
        )

    return {
        "status": TaskStatus.SUCCESS.value,
        "artifacts": {
            "card_id": task.target_id,
            "version": finalized["version"],
            "content_hash": finalized["content_hash"],
            "completeness_status": finalized["completeness_status"],
            "changed": finalized["changed"],
            **({"capture_id": capture_id} if capture_id else {}),
        },
    }


def collect_missing_reasons(
    task: Task,
    *,
    blocks: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    base_dir: Path,
    source_platform: str | None = None,
) -> list[str]:
    """Collect stable gap reasons from durable products and terminal media work."""
    reasons: list[str] = []
    for block in blocks:
        if block["block_kind"] not in _GAP_BLOCK_KINDS:
            continue
        payload = block["raw_payload"] or {}
        if block["block_kind"] == "external_doc" and payload.get("completeness") == "partial":
            reasons.append(_reason_from_payload(payload) or "external_doc_partial")
            continue
        if block["status"].lower() in {
            "resolved",
            "success",
            "enriched",
            "body_fetched",
            "done",
        }:
            continue
        reasons.append(_reason_from_payload(block["raw_payload"]) or block["block_kind"])

    for asset in assets:
        status = asset["status"].strip().lower()
        if status in _DELIVERED_ASSET_STATUSES:
            continue
        reasons.append(
            _reason_from_payload(asset["raw_payload"])
            or f"{asset['asset_kind']}_{status or 'unavailable'}"
        )

    for gap in task.artifacts_summary.get("media_gaps") or []:
        if isinstance(gap, dict):
            if str(gap.get("missing_part") or "") in _OPTIONAL_MISSING_PARTS:
                continue
            reason = _reason_from_payload(gap) or "media_unavailable"
            if reason not in _NON_GAP_MEDIA_REASONS:
                reasons.append(reason)

    reasons.extend(_upstream_failure_reasons(task, base_dir, source_platform=source_platform))
    return sorted({reason.strip() for reason in reasons if reason and reason.strip()})


def _upstream_failure_reasons(
    task: Task,
    base_dir: Path,
    *,
    source_platform: str | None,
) -> list[str]:
    from backend.application.tasks.repository import TaskRepository

    reasons: list[str] = []
    repo = TaskRepository.for_base_dir(base_dir)
    for evidence in task.artifacts_summary.get("upstream_media_tasks") or []:
        if not isinstance(evidence, dict):
            continue
        upstream_id = str(evidence.get("task_id") or "").strip()
        upstream = repo.get(upstream_id) if upstream_id else None
        if upstream and upstream.task_type in _RETIRED_UPSTREAM_TASK_TYPES:
            # Retired historical work cannot hold back current publication.
            # Video transcription remains required when the video has audio.
            continue
        status = str(
            (upstream.status.value if upstream else evidence.get("status")) or ""
        ).lower()
        if status == TaskStatus.SUCCESS.value:
            continue
        upstream_artifacts = upstream.artifacts_summary if upstream else {}
        if status == TaskStatus.SKIPPED.value and (
            upstream_artifacts.get("transcript_satisfied") is True
            or upstream_artifacts.get("transcript_not_applicable") is True
        ):
            continue
        if status == TaskStatus.SKIPPED.value and is_resolved_x_no_media_outcome(
            source_platform=source_platform,
            download_status=upstream_artifacts.get("download_status"),
            candidate_count=upstream_artifacts.get("candidate_count"),
            reasons=upstream_artifacts.get("gap_reasons") or (),
        ):
            continue
        reason = (
            _reason_from_payload(upstream_artifacts)
            or (str(upstream.error_reason).strip() if upstream and upstream.error_reason else None)
            or _reason_from_payload(evidence)
            or f"media_task_{status or 'unavailable'}"
        )
        if reason in _NON_GAP_MEDIA_REASONS:
            continue
        reasons.append(reason)
    return reasons


def _unfinished_required_media_task_ids(task: Task, base_dir: Path) -> list[str]:
    from backend.application.tasks.repository import TaskRepository

    repo = TaskRepository.for_base_dir(base_dir)
    unfinished: list[str] = []
    for evidence in task.artifacts_summary.get("upstream_media_tasks") or []:
        if not isinstance(evidence, dict):
            continue
        task_id = str(evidence.get("task_id") or "").strip()
        upstream = repo.get(task_id) if task_id else None
        if upstream is None or upstream.task_type in _RETIRED_UPSTREAM_TASK_TYPES:
            continue
        if upstream.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            unfinished.append(upstream.task_id)
    return sorted(set(unfinished))


def _reason_from_payload(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in (
        "failure_reason",
        "reason",
        "block_reason",
        "gap_reason",
        "missing_reason",
        "blocked_reason",
        "download_status",
    ):
        candidate = value.get(key)
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None
