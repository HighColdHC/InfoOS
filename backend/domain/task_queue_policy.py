"""Pure durable task queue routing, priority, and deadline policy."""
from __future__ import annotations

from backend.domain.timestamps import format_utc_seconds_z, parse_utc

from dataclasses import dataclass
from datetime import timedelta

from backend.domain.tasks import TaskQueue, TaskType

CONTENT_ENRICH_BROWSER_PLATFORMS = frozenset({"x", "xiaohongshu", "scys", "zsxq"})
MEDIA_DOWNLOAD_BROWSER_PLATFORMS = frozenset({"x", "xiaohongshu", "douyin"})


@dataclass(frozen=True)
class QueuePolicy:
    queue_name: str
    priority: int
    deadline_at: str


_PRIORITY_AND_SLA_SECONDS: dict[TaskType, tuple[int, int]] = {
    TaskType.MEDIA_DOWNLOAD: (0, 15 * 60),
    TaskType.VIDEO_EXTRACT_AUDIO: (10, 30 * 60),
    TaskType.VIDEO_TRANSCRIBE: (20, 4 * 60 * 60),
    TaskType.SOURCE_CALIBRATE: (30, 60 * 60),
    TaskType.FETCH: (30, 60 * 60),
    TaskType.CARD_BUILD: (40, 2 * 60 * 60),
    TaskType.CONTENT_ENRICH: (40, 2 * 60 * 60),
    TaskType.CARD_FINALIZE: (40, 2 * 60 * 60),
}
_DEFAULT_PRIORITY_AND_SLA_SECONDS = (90, 24 * 60 * 60)


def queue_policy_for(
    task_type: TaskType,
    *,
    platform: str,
    enqueued_at: str,
    next_run_at: str | None,
) -> QueuePolicy:
    priority, sla_seconds = _PRIORITY_AND_SLA_SECONDS.get(
        task_type, _DEFAULT_PRIORITY_AND_SLA_SECONDS
    )
    base = max(parse_utc(enqueued_at), parse_utc(next_run_at or enqueued_at))
    return QueuePolicy(
        queue_name=queue_name_for(task_type, platform=platform).value,
        priority=priority,
        deadline_at=format_utc_seconds_z(base + timedelta(seconds=sla_seconds)),
    )


def queue_name_for(task_type: TaskType, *, platform: str) -> TaskQueue:
    normalized_platform = platform.strip().lower()
    if task_type == TaskType.AUTH_QR_CAPTURE:
        return TaskQueue.BROWSER_FETCH
    if task_type in {TaskType.SOURCE_CALIBRATE, TaskType.FETCH}:
        from backend.domain.sources.registry import source_browser_platforms

        return (
            TaskQueue.BROWSER_FETCH
            if normalized_platform in source_browser_platforms()
            else TaskQueue.PUBLIC_FETCH
        )
    if task_type in {TaskType.CONTENT_ENRICH, TaskType.MEDIA_DOWNLOAD}:
        browser_platforms = (
            CONTENT_ENRICH_BROWSER_PLATFORMS
            if task_type == TaskType.CONTENT_ENRICH
            else MEDIA_DOWNLOAD_BROWSER_PLATFORMS
        )
        if normalized_platform in browser_platforms:
            return TaskQueue.BROWSER_FETCH
    if task_type in {
        TaskType.MEDIA_DOWNLOAD,
        TaskType.VIDEO_EXTRACT_AUDIO,
    }:
        return TaskQueue.URGENT_MEDIA
    if task_type == TaskType.VIDEO_TRANSCRIBE:
        return TaskQueue.GPU_ASR
    if task_type in {
        TaskType.CARD_BUILD,
        TaskType.CONTENT_ENRICH,
        TaskType.CARD_FINALIZE,
    }:
        return TaskQueue.CARD_PIPELINE
    return TaskQueue.MAINTENANCE_IO
