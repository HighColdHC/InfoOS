"""Task failure classification and RetryPolicy decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.application.tasks.types import Task, TaskStatus, TaskType
from backend.domain.failures.classifier import ClassificationError, FetchContext, classify_failure
from backend.domain.failures.retry_policy import get_retry_policy
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.webhooks.delivery import WebhookTransportError


@dataclass(frozen=True)
class TaskRetryDecision:
    status: TaskStatus
    next_run_at: str | None
    failure_class: str
    attempt: int


def classify_task_failure(task: Task, error: BaseException | str) -> FetchFailureReason:
    """Map a task-handler failure onto the shared failure taxonomy."""
    from backend.application.workflows.acquisition_failures import AcquisitionFailureError

    if isinstance(error, AcquisitionFailureError):
        return error.failure.reason
    if task.task_type == TaskType.WEBHOOK_DELIVER and isinstance(error, WebhookTransportError):
        return (
            FetchFailureReason.RATE_LIMITED
            if error.status_code == 429
            else FetchFailureReason.NETWORK_TIMEOUT
        )
    exception = error if isinstance(error, Exception) else RuntimeError(str(error))
    context = FetchContext(platform=task.target_kind, source_type=task.task_type.value,
                           http_status=getattr(exception, "code", None))
    try:
        return classify_failure(exception, context)
    except ClassificationError:
        if task.task_type == TaskType.VIDEO_TRANSCRIBE:
            return FetchFailureReason.VIDEO_TRANSCRIBE_FAILED
        if task.task_type == TaskType.MEDIA_DOWNLOAD:
            return FetchFailureReason.MEDIA_DOWNLOAD_FAILED
        if task.task_type == TaskType.DATA_EXPORT:
            return FetchFailureReason.DATA_EXPORT_FAILED
        return FetchFailureReason.OPENCLI_COMMAND_FAILED


def decide_task_retry(
    task: Task,
    *,
    reason: FetchFailureReason,
    now: str,
    platform: str | None = None,
) -> TaskRetryDecision:
    """Apply the shared policy without performing persistence or sleeping."""
    attempt = int(task.attempt) + 1
    failure_class = reason.value
    policy = get_retry_policy(reason)
    if not policy.delays:
        return TaskRetryDecision(TaskStatus.BLOCKED, None, failure_class, attempt)
    # task.max_attempts caps total executions, while policy.max_attempts counts
    # additional executions after the first failure.
    execution_cap = min(int(task.max_attempts), int(policy.max_attempts) + 1)
    if int(task.attempt) >= execution_cap:
        # BLOCKED is terminal and needs an explicit requeue. ZSXQ/X rate limits
        # recover through the automatic platform circuit, without auth recovery.
        escalate = policy.escalate_to_blocked and not (
            platform in {"zsxq", "x"} and reason == FetchFailureReason.RATE_LIMITED
        )
        status = TaskStatus.BLOCKED if escalate else TaskStatus.FAILED
        return TaskRetryDecision(status, None, failure_class, attempt)

    delay_seconds = policy.delays[min(int(task.attempt) - 1, len(policy.delays) - 1)]
    return TaskRetryDecision(
        TaskStatus.PENDING,
        _add_seconds(now, delay_seconds),
        failure_class,
        attempt,
    )


def _add_seconds(now: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=int(seconds))).astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
