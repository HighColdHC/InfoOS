from __future__ import annotations

from backend.domain.timestamps import utc_now_seconds_z

from pathlib import Path
from typing import Any

from backend.application.tasks.types import Task, TaskStatus
from backend.infrastructure.observability.logging import get_logger


def handle_fetch(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.workflows.acquisition import (
        SourceAdmissionDeniedError,
        fetch_source,
    )
    from backend.application.workflows.acquisition_failures import AcquisitionFailureError
    from backend.application.workflows.fetch_commit import SourceConfigurationChangedError
    from backend.application.workflows.browser_acquisition_batch import (
        active_browser_acquisition_batch,
        repair_browser_acquisition_batch,
    )
    from backend.domain.failures.taxonomy import FetchFailureReason
    from backend.application.workflows.acquisition_checkpoint_replay import matching_complete_checkpoint
    from backend.application.workflows.source_initial_fetch import SourceTaskSupersededError

    if (checkpoint := matching_complete_checkpoint(base_dir, task)) is not None:
        artifacts = {**checkpoint, "replayed": True}
        return {"status": TaskStatus.SUCCESS.value, "artifacts": artifacts}
    if task.artifacts_summary.get("capture_id"):
        from backend.application.workflows.capture_fetch import fetch_manual_capture

        artifacts = fetch_manual_capture(base_dir, task)
        return {"status": TaskStatus.SUCCESS.value, "artifacts": artifacts}
    deferred = _defer_outside_feed_window(task, base_dir)
    if deferred is not None:
        return deferred
    browser_batch = active_browser_acquisition_batch()
    if browser_batch is not None and browser_batch.prepare_result.get("status") != "ready":
        return {
            "status": TaskStatus.BLOCKED.value,
            "artifacts": {"browser_session": browser_batch.prepare_result},
            "error": "browser_session_not_ready",
        }
    try:
        artifacts = fetch_source(base_dir, source_id=task.target_id, task=task)
    except SourceConfigurationChangedError:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"reason": "source_configuration_changed"},
            "error": "source_configuration_changed",
        }
    except SourceAdmissionDeniedError:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"reason": "source_admission_denied"},
            "error": "source_admission_denied",
        }
    except SourceTaskSupersededError as error:
        return {
            "status": TaskStatus.SKIPPED.value,
            "artifacts": {"reason": "superseded", "superseded_by": error.winner_task_id},
            "error": "superseded",
        }
    except AcquisitionFailureError as error:
        # Browser repair is an acquisition-lane concern. Authentication and
        # configuration failures intentionally fall through to blocked retry
        # policy without launching a useless repair/retry cycle.
        if (
            browser_batch is None
            or error.failure.reason not in {
                FetchFailureReason.OPENCLI_DAEMON_DOWN,
                FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED,
            }
            or task.artifacts_summary.get("browser_repair_attempted") is True
        ):
            raise
        repair_result = repair_browser_acquisition_batch(
            browser_batch,
            original_diagnostic=error.failure.diagnostic,
            retried_at=utc_now_seconds_z(),
        )
        if repair_result.get("status") != "ready":
            return _failed_repair_result(error, browser_batch)
        try:
            artifacts = fetch_source(base_dir, source_id=task.target_id, task=task)
        except SourceConfigurationChangedError:
            return {
                "status": TaskStatus.SKIPPED.value,
                "artifacts": {"reason": "source_configuration_changed"},
                "error": "source_configuration_changed",
            }
        except SourceTaskSupersededError as error:
            return {
                "status": TaskStatus.SKIPPED.value,
                "artifacts": {"reason": "superseded", "superseded_by": error.winner_task_id},
                "error": "superseded",
            }
        except AcquisitionFailureError as retry_error:
            return _failed_repair_result(retry_error, browser_batch)
    if browser_batch is not None:
        artifacts = {
            **artifacts,
            "browser_batch": {
                "source_ids": browser_batch.source_ids,
                "repair_attempts": browser_batch.repair_attempts,
            },
        }
    get_logger("tasks.fetch").info(
        "fetch_completed source_id=%s status=%s raw_item_count=%s",
        task.target_id,
        TaskStatus.SUCCESS.value,
        len(artifacts["raw_item_ids"]),
    )
    return {
        "status": TaskStatus.SUCCESS.value,
        "artifacts": artifacts,
    }




def _defer_outside_feed_window(task: Task, base_dir: Path) -> dict[str, Any] | None:
    from backend.domain.sources.douyin_home_feed import first_session_at, is_douyin_home_feed
    from backend.domain.timestamps import format_utc_seconds_z, parse_utc
    from backend.infrastructure.storage.repositories import SourceRepository

    # Initial calibration, explicit manual runs and failure retries keep their policy.
    if task.attempt != 1 or any(task.artifacts_summary.get(key) for key in ("initial_fetch", "manual_run")):
        return None
    source = SourceRepository(base_dir).get_source(task.target_id)
    if source is None or not is_douyin_home_feed(source):
        return None
    now = parse_utc(utc_now_seconds_z())
    scheduled = first_session_at(now, source.get("raw_metadata"))
    if scheduled == now:
        return None
    return {"status": TaskStatus.PENDING.value, "next_run_at": format_utc_seconds_z(scheduled),
            "artifacts": {"reason": "outside_active_hours"}, "error": "outside_active_hours"}


def _failed_repair_result(error: BaseException, browser_batch: Any) -> dict[str, Any]:
    """Keep repair evidence when the task proceeds to normal retry policy."""
    return {
        "status": TaskStatus.FAILED.value,
        "artifacts": {
            "repair_attempts": list(browser_batch.repair_attempts),
            "browser_repair_attempted": True,
        },
        "error": error,
    }


def handle_card_build(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.pipeline.information_card_builder import (
        build_information_card,
        information_card_payload,
    )
    from backend.application.tasks.input_versions import enrichment_input_version
    from backend.application.pipeline.normalize import normalize_raw_item
    from backend.application.tasks.planner import parse_raw_item_target_id
    from backend.domain.models.raw_item import RawItem
    from backend.infrastructure.storage.repositories import CardRepository, RawItemRepository

    raw_item_repo = RawItemRepository(base_dir)
    target = parse_raw_item_target_id(task.target_id)
    raw_record = (
        raw_item_repo.get_for_connector(*target)
        if target is not None
        else raw_item_repo.get(task.target_id)
    )
    if raw_record is None:
        return {
            "status": TaskStatus.BLOCKED.value,
            "artifacts": {"raw_item_id": task.target_id},
            "error": "raw_item_missing",
        }

    raw_item = RawItem.model_validate(raw_record["raw_payload"])
    card = build_information_card(
        normalize_raw_item(raw_item),
        raw_item,
        source_run_id=raw_record["run_id"],
        first_seen_at=raw_record["first_seen_at"],
    )
    card_repo = CardRepository(base_dir)
    existing = card_repo.get_card(card.card_id)
    payload = information_card_payload(card)
    if raw_item.source_platform == "zsxq":
        from backend.application.pipeline.zsxq_observations import reuse_zsxq_card
        match, unchanged = reuse_zsxq_card(base_dir, raw_item, payload)
        existing = match or existing
        card.card_id = payload["card_id"]
        if unchanged and existing is not None:
            return {"status": TaskStatus.SUCCESS.value, "artifacts": {
                "card_id": card.card_id, "raw_item_id": raw_item.raw_item_id,
                "created_or_updated": "updated", "content_unchanged": True}}
    if (
        existing is not None
        and existing["status"] == "active"
        and enrichment_input_version(existing) == enrichment_input_version(payload)
    ):
        # A duplicate build of identical durable input must not undo a
        # successful finalize. Changed content still starts a fresh cycle.
        payload.update(
            {
                "status": existing["status"],
                "completeness_status": existing["completeness_status"],
                "missing_reasons": existing["missing_reasons"],
                "workflow_status": existing["workflow_status"],
            }
        )
    created_or_updated = "updated" if existing is not None else "created"
    card_repo.upsert_card(payload)
    from backend.application.pipeline.prefilter_blocks import preserve_prefilter_blocks
    preserve_prefilter_blocks(base_dir, card.card_id, raw_item)
    capture_id = str(task.artifacts_summary.get("capture_id") or "")
    if capture_id:
        from backend.infrastructure.storage.base import utc_now
        from backend.infrastructure.storage.capture_receipts import (
            CaptureReceiptRepository,
        )

        CaptureReceiptRepository(base_dir).update_processing(
            capture_id,
            processing_status="running",
            card_id=card.card_id,
            updated_at=utc_now(),
        )
    return {
        "status": TaskStatus.SUCCESS.value,
        "artifacts": {
            "card_id": card.card_id,
            "raw_item_id": raw_item.raw_item_id,
            "created_or_updated": created_or_updated,
            **({"capture_id": capture_id} if capture_id else {}),
        },
    }


def handle_content_enrich(task: Task, base_dir: Path) -> dict[str, Any]:
    from backend.application.enrichment.task_blocks import enrich_card_blocks_for_task
    from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository

    before_ids = {block["block_id"] for block in CardContentBlockRepository(base_dir).list_for_card(task.target_id)}
    if task.artifacts_summary.get("feishu_backfill"):
        from backend.application.enrichment.external_docs import enrich_external_docs

        blocks_added = enrich_external_docs(
            task.target_id, base_dir, document_url=str(task.artifacts_summary.get("document_url") or ""),
            force_browser=True)
    else:
        card = CardRepository(base_dir).get_card(task.target_id)
        from backend.application.enrichment.zsxq_article import ArticleCircuitOpen
        from backend.application.tasks.execution_clock import add_seconds

        try:
            blocks_added = enrich_card_blocks_for_task(
                task.target_id, base_dir, card or {},
                external_doc_force_browser=bool(task.artifacts_summary.get("external_doc_retry")))
        except ArticleCircuitOpen as exc:
            return {"status": TaskStatus.PENDING.value,
                    "next_run_at": max(exc.retry_at or "", add_seconds(utc_now_seconds_z(), 60)),
                    "error": str(exc), "artifacts": {"reason": "zsxq_article_platform_circuit_open"}}
    blocks = CardContentBlockRepository(base_dir).list_for_card(task.target_id)
    new_blocks = [block for block in blocks if block["block_id"] not in before_ids]
    gap_reasons = []
    for block in new_blocks:
        raw_payload = block.get("raw_payload") if isinstance(block.get("raw_payload"), dict) else {}
        reason = raw_payload.get("reason") or raw_payload.get("block_reason")
        if reason:
            gap_reasons.append(str(reason))
    enriched_statuses = {"enriched", "body_fetched", "done", "preserved", "downloaded"}
    blocked_statuses = {"blocked"}
    pending_statuses = {"pending"}
    subtitle_outcome = next(
        (
            dict(block["raw_payload"])
            for block in blocks
            if block["block_id"].endswith("_youtube_subtitle_attempt")
            and isinstance(block.get("raw_payload"), dict)
        ),
        None,
    )
    return {
        "status": TaskStatus.SUCCESS.value,
        "artifacts": {
            "blocks_added": blocks_added,
            "new_blocks": len(new_blocks),
            "enriched_blocks": sum(1 for block in new_blocks if block["status"] in enriched_statuses),
            "blocked_blocks": sum(1 for block in new_blocks if block["status"] in blocked_statuses),
            "pending_blocks": sum(1 for block in new_blocks if block["status"] in pending_statuses),
            "gap_reasons": sorted(set(gap_reasons)),
            **({"subtitle_outcome": subtitle_outcome} if subtitle_outcome else {}),
        },
    }
