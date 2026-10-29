from __future__ import annotations

from backend.application.tasks.types import TaskType
from backend.domain.task_queue_policy import (
    CONTENT_ENRICH_BROWSER_PLATFORMS as CONTENT_ENRICH_BROWSER_PLATFORMS,
    MEDIA_DOWNLOAD_BROWSER_PLATFORMS as MEDIA_DOWNLOAD_BROWSER_PLATFORMS,
)

FETCH_QUEUE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType.AUTH_QR_CAPTURE,
    TaskType.SOURCE_CALIBRATE,
    TaskType.FETCH,
)

ENRICHMENT_QUEUE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType.CARD_BUILD,
    TaskType.CONTENT_ENRICH,
    TaskType.MEDIA_DOWNLOAD,
    TaskType.VIDEO_EXTRACT_AUDIO,
    TaskType.VIDEO_TRANSCRIBE,
    TaskType.CARD_FINALIZE,
)

LOCAL_IO_QUEUE_TASK_TYPES: tuple[TaskType, ...] = (TaskType.DATA_EXPORT,)


MAINTENANCE_QUEUE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType.DAILY_BRIEF_BUILD,
    TaskType.PIPELINE_RECONCILE,
    TaskType.TASK_EVENT_ARCHIVE,
    TaskType.NOTIFICATION_DELIVER,
    TaskType.AUTH_QR_CHECK,
    TaskType.WEBHOOK_DELIVER,
)

SEMANTIC_QUEUE_TASK_TYPES: tuple[TaskType, ...] = (
    TaskType.SEMANTIC_EXTRACT,
    TaskType.SCORE,
    TaskType.DECIDE,
    TaskType.OUTPUT_GENERATE,
    TaskType.TOPIC_UPDATE,
    TaskType.ACTION_EXECUTE,
    TaskType.CLUSTER,
    TaskType.DAILY_BRIEF,
)
