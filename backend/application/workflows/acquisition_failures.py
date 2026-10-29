"""One failure payload for public and browser source acquisition."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.failures.classifier import ClassificationError, FetchContext, classify_failure
from backend.domain.failures.connector_diagnostics import classify_connector_diagnostic
from backend.domain.failures.taxonomy import FetchFailureReason
from backend.infrastructure.security.redaction import summarize_subprocess_output


@dataclass(frozen=True)
class AcquisitionFailure:
    reason: FetchFailureReason
    message: str
    retryable: bool
    user_action: str | None
    diagnostic: dict[str, Any]


class AcquisitionFailureError(RuntimeError):
    """Carries a classified acquisition failure to the task retry boundary."""

    def __init__(self, failure: AcquisitionFailure):
        super().__init__(failure.message)
        self.failure = failure


def classify_acquisition_failure(
    *,
    source: dict[str, Any],
    result: dict[str, Any] | None,
    error: BaseException | None,
) -> AcquisitionFailure | None:
    """Classify connector output and exceptions without deciding persistence.

    A successful empty result is valid. An empty browser result accompanied by a
    render diagnostic is not; connector diagnostics remain attached verbatim so
    repairs can be audited after a later successful retry.
    """
    platform = str(source.get("source_platform") or source.get("platform") or "")
    connector_errors = _errors_from(result, None)
    errors = _errors_from(result, error)
    if not errors and error is None:
        return None
    diagnostic = classify_connector_diagnostic(connector_errors or errors, platform=platform) or {}
    # Connector diagnostics are platform-aware and therefore outrank generic
    # exception text (for example a Douyin render diagnostic containing the
    # word "timeout").
    reason = _reason_from_diagnostic(diagnostic)
    if error is not None and diagnostic.get("category") == "connector_error":
        reason = None
    if reason is None and error is not None:
        reason = _classify_error(error, platform=platform, source=source)
    is_rss = platform == "rss" or source.get("type") == "rss"
    if is_rss and getattr(getattr(error, "response", None), "status_code", None) in {404, 410}:
        reason = FetchFailureReason.SOURCE_UNAVAILABLE
        diagnostic = {**diagnostic, "category": "source_unavailable", "retryable": False, "severity": "blocked"}
    if reason is None:
        return None
    diagnostic = {
        **diagnostic,
        **_raw_response_reference(errors),
        **_stderr_summary_reference(error),
    }
    return AcquisitionFailure(
        reason=reason,
        message=str(diagnostic.get("summary") or str(error) or reason.value),
        retryable=reason not in {
            FetchFailureReason.PLATFORM_NOT_LOGGED_IN,
            FetchFailureReason.CHROME_NOT_CONFIGURED,
            FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED,
            FetchFailureReason.PARSE_FAILED,
            FetchFailureReason.SOURCE_UNAVAILABLE,
        },
        user_action=(_default_action(reason, platform) if reason == FetchFailureReason.SOURCE_UNAVAILABLE
                     else diagnostic.get("action") or _default_action(reason, platform)),
        diagnostic=diagnostic,
    )


def _errors_from(result: dict[str, Any] | None, error: BaseException | None) -> list[dict[str, Any]]:
    errors = list((result or {}).get("errors") or [])
    normalized = [item if isinstance(item, dict) else {"message": str(item)} for item in errors]
    if error is not None:
        summary = getattr(error, "stderr_summary", None)
        safe_summary = summarize_subprocess_output(None, summary) if isinstance(summary, (str, bytes)) else None
        normalized.append({
            "message": str(safe_summary or error),
            "exception_type": type(error).__name__,
        })
    return normalized


def _classify_error(error: BaseException, *, platform: str, source: dict[str, Any]) -> FetchFailureReason:
    exception = error if isinstance(error, Exception) else RuntimeError(str(error))
    try:
        return classify_failure(exception, FetchContext(platform=platform, source_type=str(source.get("source_type") or "fetch")))
    except ClassificationError:
        return _reason_from_diagnostic(
            classify_connector_diagnostic([{"message": str(error)}], platform=platform) or {}
        ) or FetchFailureReason.OPENCLI_COMMAND_FAILED


def _reason_from_diagnostic(diagnostic: dict[str, Any]) -> FetchFailureReason | None:
    category = str(diagnostic.get("category") or "")
    summary = str(diagnostic.get("summary") or "").lower()
    if category == "source_unavailable":
        return FetchFailureReason.SOURCE_UNAVAILABLE
    if category == "auth_required":
        return FetchFailureReason.PLATFORM_NOT_LOGGED_IN
    if category == "chrome_not_configured":
        return FetchFailureReason.CHROME_NOT_CONFIGURED
    if category == "page_load":
        return FetchFailureReason.BROWSER_PAGE_LOAD_FAILED
    if category == "challenge_required":
        return FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED
    if category == "rate_limited":
        return FetchFailureReason.RATE_LIMITED
    if category == "command_execution":
        return FetchFailureReason.OPENCLI_COMMAND_FAILED
    if category == "timeout" or category == "network":
        return FetchFailureReason.NETWORK_TIMEOUT
    if category == "invalid_output":
        return FetchFailureReason.PARSE_FAILED
    if category == "browser_session":
        if diagnostic.get("reason") in {"douyin_render_blocked", "douyin_challenge_required"}:
            return FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED
        if "configured" in summary or "profile" in summary and "not found" in summary:
            return FetchFailureReason.CHROME_NOT_CONFIGURED
        return FetchFailureReason.OPENCLI_DAEMON_DOWN
    if "rate limit" in summary or "429" in summary:
        return FetchFailureReason.RATE_LIMITED
    return FetchFailureReason.OPENCLI_COMMAND_FAILED if diagnostic else None


def _raw_response_reference(errors: list[dict[str, Any]]) -> dict[str, Any]:
    for error in errors:
        for key in ("raw_response_ref", "raw_response_reference"):
            if error.get(key):
                return {"raw_response_ref": error[key]}
    return {}


def _stderr_summary_reference(error: BaseException | None) -> dict[str, Any]:
    summary = getattr(error, "stderr_summary", None) if error is not None else None
    if not isinstance(summary, (str, bytes)) or not summary:
        return {}
    return {"stderr_summary": summarize_subprocess_output(None, summary)}


def _default_action(reason: FetchFailureReason, platform: str) -> str | None:
    if reason == FetchFailureReason.SOURCE_UNAVAILABLE:
        return ("Check the source account name or feed URL; if the account has been deleted, "
                "suspended, or made private, modify or disable this source.")
    if reason == FetchFailureReason.PLATFORM_NOT_LOGGED_IN:
        return f"Log in to {platform or 'the platform'} in the dedicated InfoOS browser profile, then rerun this source."
    if reason == FetchFailureReason.CHROME_NOT_CONFIGURED:
        return "Complete the dedicated InfoOS browser setup before rerunning this source."
    if reason == FetchFailureReason.BROWSER_PAGE_LOAD_FAILED:
        return "Retry the page load; if it repeats, inspect browser navigation and upstream connectivity."
    if reason == FetchFailureReason.PLATFORM_CHALLENGE_REQUIRED:
        return "Complete the platform security verification in the dedicated InfoOS browser, then retry this source."
    if reason == FetchFailureReason.PARSE_FAILED:
        return "Inspect connector output mapping before retrying."
    if reason == FetchFailureReason.BROWSER_SESSION_RENDER_BLOCKED:
        return "Check Douyin login status and security verification/challenge, then retry this source."
    return None
