"""Pure task retry eligibility rules.

The task API and read models must agree on when a normal task can be retried.
Provider-bound ``CARD_LLM_ENRICH`` recovery is intentionally evaluated by its
existing provider recovery diagnostic; this module only owns the primitive
status/attempt rule shared by ordinary task projections and use cases.
"""
from __future__ import annotations

import re
from typing import Any

from backend.domain.tasks import Task, TaskStatus


RETRYABLE_TASK_STATUSES = frozenset({TaskStatus.FAILED, TaskStatus.BLOCKED})


def is_task_retry_eligible(task: Task) -> bool:
    """Return whether a normal task has a retryable terminal state."""
    return task.status in RETRYABLE_TASK_STATUSES and task.attempt < task.max_attempts


__all__ = [
    "RETRYABLE_TASK_STATUSES",
    "build_retry_event_payload",
    "is_task_retry_eligible",
]


_SAFE_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SAFE_INT = re.compile(r"^-?\d{1,10}$")
_EXIT_TEXT = re.compile(
    r"\b(?:exit\s+(?:code|status)|exit_code|returncode)\s*[:=]?\s*(-?\d{1,10})\b",
    re.IGNORECASE,
)
_TIMEOUT_TEXT = re.compile(
    r"\b(?:timed?\s+out|timeout)\s+after\s+(\d{1,8}(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b",
    re.IGNORECASE,
)


def build_retry_event_payload(
    error: BaseException | str,
    *,
    attempt: int,
    next_run_at: str | None,
    failure_class: str,
) -> dict[str, Any]:
    """Build bounded retry evidence without serializing exception details."""
    diagnostic: dict[str, Any] = {
        "type": _safe_type(error),
        "code": str(failure_class),
    }
    exit_code = _numeric_attribute(error, ("returncode", "exit_code"), integer=True)
    timeout_seconds = _numeric_attribute(error, ("timeout", "timeout_seconds"))
    try:
        text = str(error)
    except Exception:  # noqa: BLE001 - Foreign exception __str__ implementations may raise; retry failure evidence must still be persisted
        text = ""
    if exit_code is None:
        match = _EXIT_TEXT.search(text)
        exit_code = _numeric(match.group(1), integer=True) if match else None
    if timeout_seconds is None:
        match = _TIMEOUT_TEXT.search(text)
        timeout_seconds = _numeric(match.group(1)) if match else None
    if exit_code is not None:
        diagnostic["exit_code"] = exit_code
    is_timeout = (
        isinstance(error, TimeoutError)
        or type(error).__name__ == "TimeoutExpired"
        or "timeout" in str(failure_class).lower()
        or timeout_seconds is not None
    )
    if is_timeout:
        diagnostic["timed_out"] = True
    if timeout_seconds is not None:
        diagnostic["timeout_seconds"] = timeout_seconds
    return {
        "attempt": int(attempt),
        "next_run_at": next_run_at,
        "diagnostic": diagnostic,
    }


def _safe_type(error: BaseException | str) -> str:
    name = type(error).__name__
    return name if _SAFE_TYPE.fullmatch(name) else "Exception"


def _numeric_attribute(
    error: Any,
    names: tuple[str, ...],
    *,
    integer: bool = False,
) -> int | float | None:
    for name in names:
        try:
            value = getattr(error, name, None)
        except Exception:  # noqa: BLE001, S112 - Foreign exception descriptors may raise; skip unreadable numeric evidence so failure persistence can continue
            continue
        result = _numeric(value, integer=integer)
        if result is not None and (not integer or isinstance(result, int)):
            return result
    return None


def _numeric(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        text = str(value).strip()
        if integer and not _SAFE_INT.fullmatch(text):
            return None
        number = float(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or abs(number) > 2**31 - 1 or (integer and not number.is_integer()):
        return None
    return int(number) if number.is_integer() else number
