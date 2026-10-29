"""Stable task queue value objects shared across backend layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from backend.domain.json_types import JsonObject


class TaskType(str, Enum):
    SOURCE_CALIBRATE = "source_calibrate"
    FETCH = "fetch"
    CARD_BUILD = "card_build"
    CONTENT_ENRICH = "content_enrich"
    MEDIA_DOWNLOAD = "media_download"
    IMAGE_UNDERSTAND = "image_understand"
    VIDEO_EXTRACT_AUDIO = "video_extract_audio"
    VIDEO_TRANSCRIBE = "video_transcribe"
    CARD_FINALIZE = "card_finalize"
    DAILY_BRIEF_BUILD = "daily_brief_build"
    CARD_LLM_ENRICH = "card_llm_enrich"
    NOTIFICATION_DELIVER = "notification_deliver"
    AUTH_QR_CAPTURE = "auth_qr_capture"
    AUTH_QR_CHECK = "auth_qr_check"
    WERSS_SYNC = "werss_sync"
    WEBHOOK_DELIVER = "webhook_deliver"
    DATA_EXPORT = "data_export"
    CONTEXT_PACK = "context_pack"
    SUMMARY = "summary"
    SEMANTIC_EXTRACT = "semantic_extract"
    SCORE = "score"
    DECIDE = "decide"
    OUTPUT_GENERATE = "output_generate"
    TOPIC_UPDATE = "topic_update"
    ACTION_EXECUTE = "action_execute"
    PIPELINE_RECONCILE = "pipeline_reconcile"
    TASK_EVENT_ARCHIVE = "task_event_archive"
    CLUSTER = "cluster"
    DAILY_BRIEF = "daily_brief"


RETIRED_TASK_TYPES = frozenset({
    TaskType.SEMANTIC_EXTRACT, TaskType.SCORE, TaskType.DECIDE,
    TaskType.OUTPUT_GENERATE, TaskType.TOPIC_UPDATE, TaskType.ACTION_EXECUTE,
    TaskType.CLUSTER, TaskType.DAILY_BRIEF,
    TaskType.CARD_LLM_ENRICH, TaskType.IMAGE_UNDERSTAND,
    TaskType.SUMMARY, TaskType.CONTEXT_PACK, TaskType.WERSS_SYNC,
})


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


class TaskQueue(str, Enum):
    PUBLIC_FETCH = "public_fetch"
    BROWSER_FETCH = "browser_fetch"
    URGENT_MEDIA = "urgent_media"
    CARD_PIPELINE = "card_pipeline"
    GPU_ASR = "gpu_asr"
    CLOUD_AI = "cloud_ai"
    MAINTENANCE_IO = "maintenance_io"


TASK_QUEUE_NAMES: tuple[str, ...] = tuple(queue.value for queue in TaskQueue)


LEGACY_INPUT_VERSION = "v1"


def fetch_circuit_source_id(scope_key: str | None) -> str | None:
    """RSS feed circuits retain platform scope but identify one source."""
    return scope_key[4:] if scope_key and scope_key.startswith("rss:") else None


def fetch_circuit_platform(scope_key: str | None) -> str | None:
    """Platform-specific policies must receive a platform, not a circuit key."""
    return "rss" if fetch_circuit_source_id(scope_key) is not None else scope_key


def new_task_completion_token() -> str:
    return uuid4().hex


@dataclass(frozen=True)
class DependencySpec:
    task_id: str
    required_status: TaskStatus


@dataclass(frozen=True)
class Task:
    task_id: str
    task_type: TaskType
    target_kind: str
    target_id: str
    status: TaskStatus = TaskStatus.PENDING
    depends_on: tuple[DependencySpec, ...] = ()
    attempt: int = 1
    max_attempts: int = 3
    next_run_at: str | None = None
    error_reason: str | None = None
    artifacts_summary: JsonObject = field(default_factory=dict)
    claimed_by: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    input_version: str = LEGACY_INPUT_VERSION
    result_version: str | None = None
    last_failure_class: str | None = None
    completion_token: str | None = None
    queue_name: str | None = None
    priority: int | None = None
    deadline_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def can_run(self, completed: dict[str, TaskStatus]) -> bool:
        return all(completed.get(dep.task_id) == dep.required_status for dep in self.depends_on)


def is_task_cancelled(task: Task) -> bool:
    """An operator cancellation is terminal without a stage result to advance."""
    return task.status == TaskStatus.SKIPPED and task.error_reason == "operator_cancelled"
