from __future__ import annotations

from typing import TypedDict

from backend.domain.json_types import JsonObject

from backend.domain.tasks import Task, TaskStatus


class TaskOperation(TypedDict):
    status: str
    bucket: str
    reason: str | None
    next_action: str
    deferred_until: object
    throttle_key: object
    rate_limit_reason: object


_AUTH_MARKERS = (
    "auth_required",
    "platform_not_logged_in",
    "not_logged_in",
    "not logged in",
    "login_required",
    "needs_login",
    "no ct0 cookie",
)

_LOW_SIGNAL_MARKERS = (
    "low_signal",
    "low-signal",
    "insufficient_signal",
    "no_summary_signal",
)

def classify_task_operation(task: Task) -> TaskOperation:
    artifacts = task.artifacts_summary
    diagnostic = _failure_diagnostic(artifacts)
    reason = _first_text(
        task.error_reason,
        artifacts.get("error_message"),
        artifacts.get("error_summary"),
        artifacts.get("error_reason"),
        artifacts.get("error"),
        artifacts.get("reason"),
        artifacts.get("rate_limit_reason"),
        diagnostic.get("action"),
        diagnostic.get("category"),
    )
    combined = " ".join(
        str(value)
        for value in (
            reason,
            artifacts.get("diagnostic"),
            artifacts.get("message"),
            artifacts.get("status"),
            diagnostic.get("category"),
        )
        if value
    ).lower()
    if _is_rate_limited(task, artifacts, reason):
        status = "deferred"
        next_action = "wait_until_deferred"
    elif _contains_any(combined, _AUTH_MARKERS):
        status = "auth_required"
        next_action = "refresh_browser_login"
    elif task.status == TaskStatus.RUNNING:
        status = "running"
        next_action = "watch_progress"
    elif task.status == TaskStatus.PENDING and (task.attempt > 1 or reason):
        status = "retrying"
        next_action = "wait_for_retry"
    elif task.status == TaskStatus.PENDING:
        status = "pending"
        next_action = "wait_for_worker"
    elif task.status == TaskStatus.BLOCKED:
        status = "blocked"
        next_action = "fix_blocker"
    elif task.status == TaskStatus.FAILED:
        status = "failed"
        next_action = "retry_or_inspect"
    elif task.status == TaskStatus.SKIPPED and _contains_any(combined, _LOW_SIGNAL_MARKERS):
        status = "skipped_low_signal"
        next_action = "no_action"
    elif task.status == TaskStatus.SKIPPED:
        status = "skipped"
        next_action = "no_action"
    else:
        status = "success"
        next_action = "no_action"
    return _operation_payload(
        status=status,
        reason=reason,
        next_action=next_action,
        task=task,
        artifacts=artifacts,
    )


def _failure_diagnostic(artifacts: JsonObject) -> JsonObject:
    for key in ("connector_diagnostic", "failure_diagnostic"):
        diagnostic = artifacts.get(key)
        if isinstance(diagnostic, dict):
            return diagnostic
    return {}


def _operation_payload(
    *,
    status: str,
    reason: str | None,
    next_action: str,
    task: Task,
    artifacts: JsonObject,
) -> TaskOperation:
    return {
        "status": status,
        "bucket": status,
        "reason": reason,
        "next_action": next_action,
        "deferred_until": artifacts.get("deferred_until") or task.next_run_at,
        "throttle_key": artifacts.get("throttle_key"),
        "rate_limit_reason": artifacts.get("rate_limit_reason"),
    }


def _is_rate_limited(task: Task, artifacts: JsonObject, reason: str | None) -> bool:
    return bool(
        artifacts.get("rate_limited") is True
        or (reason or "").startswith("fetch_rate_limited")
        or (reason or "").lower() == "rate_limited"
        or (task.status == TaskStatus.PENDING and artifacts.get("rate_limit_reason"))
    )


def _contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


def _first_text(*values: object) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return None
