from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.application.tasks.handlers.acquisition import handle_card_build, handle_content_enrich, handle_fetch
from backend.application.tasks.handlers.card_finalize import handle_card_finalize
from backend.application.usecases.daily_brief import build_daily_brief
from backend.application.usecases.daily_brief_digest import enqueue_daily_brief_digest
from backend.application.tasks.handlers.notification_deliver import handle_notification_deliver
from backend.application.tasks.handlers.auth_qr import handle_auth_qr_capture, handle_auth_qr_check
from backend.application.tasks.handlers.webhook_deliver import handle_webhook_deliver
from backend.application.tasks.handlers.data_export import handle_data_export
from backend.application.tasks.handlers.active_maintenance import (
    handle_pipeline_reconcile,
)
from backend.application.tasks.handlers.task_event_archive import handle_task_event_archive
from backend.application.tasks.handlers.media_download import handle_media_download
from backend.application.tasks.handlers.source_calibration import handle_source_calibration
from backend.application.tasks.handlers.media_intelligence import (
    handle_video_extract_audio,
    handle_video_transcribe,
)
from backend.application.tasks.types import Task, TaskType

from backend.domain.intelligence.daily_brief import profile_retry_at
from backend.domain.tasks import RETIRED_TASK_TYPES

TaskHandler = Callable[[Task, Path], dict[str, Any]]


def _handle_daily_brief(task: Task, base_dir: Path) -> dict[str, Any]:
    result = build_daily_brief(base_dir, task.target_id)
    if result.get("reason") == "profile_missing":
        next_run_at = profile_retry_at(task.target_id, datetime.now(timezone.utc))
        if next_run_at is not None:
            return {"status": "pending", "next_run_at": next_run_at,
                    "error": "profile_missing", "artifacts": result}
    if result["status"] in ("generated", "frozen"):
        # The outbox dedupe key makes a retry after a failed enqueue resend exactly once.
        result["digest"] = {"notification_ids": enqueue_daily_brief_digest(base_dir, result["brief"])}
    return {"status": "skipped" if result["status"] == "skipped" else "success",
            "artifacts": result}


def handler_for(task_type: TaskType, *, now: str | None = None) -> TaskHandler:
    if task_type in RETIRED_TASK_TYPES:
        return lambda task, base_dir: {"status": "skipped", "artifacts": {"reason": "feature_retired"}}

    def deliver_notification(task: Task, base_dir: Path) -> dict[str, Any]:
        return handle_notification_deliver(task, base_dir, now=now)

    handlers: dict[TaskType, TaskHandler] = {
        TaskType.SOURCE_CALIBRATE: handle_source_calibration,
        TaskType.FETCH: handle_fetch,
        TaskType.CARD_BUILD: handle_card_build,
        TaskType.CONTENT_ENRICH: handle_content_enrich,
        TaskType.MEDIA_DOWNLOAD: handle_media_download,
        TaskType.VIDEO_EXTRACT_AUDIO: handle_video_extract_audio,
        TaskType.VIDEO_TRANSCRIBE: handle_video_transcribe,
        TaskType.CARD_FINALIZE: handle_card_finalize,
        TaskType.DAILY_BRIEF_BUILD: _handle_daily_brief,
        TaskType.NOTIFICATION_DELIVER: deliver_notification,
        TaskType.AUTH_QR_CAPTURE: handle_auth_qr_capture,
        TaskType.AUTH_QR_CHECK: handle_auth_qr_check,
        TaskType.WEBHOOK_DELIVER: handle_webhook_deliver,
        TaskType.DATA_EXPORT: handle_data_export,
        TaskType.PIPELINE_RECONCILE: handle_pipeline_reconcile,
        TaskType.TASK_EVENT_ARCHIVE: handle_task_event_archive,
    }
    return handlers[task_type]
