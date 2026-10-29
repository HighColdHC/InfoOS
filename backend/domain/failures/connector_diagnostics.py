from __future__ import annotations

import re
from typing import Any


_SUMMARY_LIMIT = 1000
_SUMMARY_TRUNCATION_MARKER = "\n...[diagnostic middle truncated]...\n"


def classify_connector_diagnostic(errors: list[dict[str, Any]], *, platform: str | None = None) -> dict[str, Any] | None:
    messages = [str(error.get("message") or error) for error in errors if error]
    if not messages:
        return None
    full_summary = "; ".join(messages)
    summary = _bounded_summary(full_summary)
    normalized = full_summary.lower()
    summary_metadata = {
        "summary": summary,
        "summary_truncated": len(full_summary) > _SUMMARY_LIMIT,
        "raw_length": len(full_summary),
    }

    if platform == "x" and re.search(r"x account @[a-z0-9_]+ not found or unavailable\b", normalized):
        return {
            "category": "source_unavailable",
            "failure_stage": "source_lookup",
            "severity": "blocked",
            "retryable": False,
            **summary_metadata,
        }

    if platform == "zsxq" and any(marker in normalized for marker in (
        "1059", "非官方工具", "rate_limited", "zsxq risk control",
    )):
        return {
            "category": "rate_limited",
            "reason": "zsxq_risk_control",
            "failure_stage": "platform_request",
            "severity": "warning",
            "retryable": True,
            **summary_metadata,
            "action": "知识星球风控，等待自然冷却后自动重试；重试耗尽后本次任务失败，可稍后重新发起。",
        }

    structured_evidence = " ".join(
        str(error.get(key) or "")
        for error in errors
        if isinstance(error, dict)
        for key in ("evidence", "page_title", "challenge_url", "iframe_url")
    ).lower()
    challenge_markers = (
        "验证码中间页",
        "rmc.bytedance.com",
        "douyin_challenge_required",
        "captcha",
        "captcha required",
        "验证码",
        "security verification required",
    )
    if platform == "douyin" and any(
        marker in normalized or marker in structured_evidence
        for marker in challenge_markers
    ):
        return {
            "category": "challenge_required",
            "reason": "douyin_challenge_required",
            "failure_stage": "platform_verification",
            "evidence": "douyin_challenge_required",
            "severity": "blocked",
            "retryable": False,
            **summary_metadata,
            "action": "Open Douyin in the dedicated InfoOS browser profile and complete the security verification.",
        }

    auth_required_patterns = (
        "auth_required",
        "login required",
        "auth/permission error",
        "no ct0 cookie",
        "not logged into x.com",
        "not logged in to x.com",
        "not logged in to reddit",
        "reddit /api/me.json returned http 403",
        "log in to https://reddit.com",
    )
    reddit_explicit_auth = platform == "reddit" and (
        ("whoami" in normalized and any(marker in normalized for marker in ("403", "forbidden", "login")))
        or ("reddit" in normalized and "403" in normalized)
    )
    if any(pattern in normalized for pattern in auth_required_patterns) or reddit_explicit_auth or (platform == "x" and "auth failed" in normalized):
        return {
            "category": "auth_required",
            "failure_stage": "platform_authentication",
            "severity": "blocked",
            "retryable": False,
            **summary_metadata,
            "action": _auth_required_action(platform),
        }

    chrome_not_configured_patterns = (
        "chrome_not_configured",
        "chrome not configured",
        "browser-session.json",
        "no browser profile",
    )
    if any(pattern in normalized for pattern in chrome_not_configured_patterns):
        return {
            "category": "chrome_not_configured",
            "failure_stage": "browser_setup",
            "severity": "blocked",
            "retryable": False,
            **summary_metadata,
            "action": "Complete the dedicated InfoOS browser setup before rerunning this source.",
        }

    browser_session_patterns = (
        "browser session",
        "browser bridge",
        "browser_connect",
        "exit code 69",
        "extension not connected",
        "extension disconnected",
        "no tab with id",
        "stale page identity",
        "target closed",
        "target navigated",
        "pre-navigation to https://x.com failed",
    )
    opencli_profile_disconnected = "browser profile" in normalized and "is not connected" in normalized
    if any(pattern in normalized for pattern in browser_session_patterns) or opencli_profile_disconnected:
        return {
            "category": "browser_session",
            "failure_stage": "browser_session",
            "severity": "blocked",
            "retryable": True,
            **summary_metadata,
            "action": "Open the dedicated InfoOS browser profile and reconnect Browser Bridge.",
        }

    browser_target_detached_patterns = (
        "detached while handling command",
        "debugger is not attached to the tab",
    )
    if any(pattern in normalized for pattern in browser_target_detached_patterns):
        return {
            "category": "command_execution",
            "reason": "browser_target_detached",
            "failure_stage": "browser_target_execution",
            "severity": "error",
            "retryable": True,
            **summary_metadata,
            "action": "Retry the read-only browser command; if detachment repeats, inspect the dedicated Chrome target and Browser Bridge connection before rerunning.",
        }

    page_load_patterns = (
        "page.goto",
        "page load failed",
        "navigation failed",
        "navigation timeout",
        "net::err_",
        "pre-navigation to ",
    )
    if any(pattern in normalized for pattern in page_load_patterns):
        return {
            "category": "page_load",
            "reason": "browser_page_load_failed",
            "failure_stage": "page_load",
            "severity": "warning",
            "retryable": True,
            **summary_metadata,
            "action": "Retry the page load; if it repeats, inspect browser navigation and upstream connectivity.",
        }
    if platform == "douyin" and "did not render result cards" in normalized:
        return {
            "category": "browser_session",
            "reason": "douyin_render_blocked",
            "failure_stage": "page_render",
            "evidence": "douyin_render_blocked",
            "severity": "blocked",
            "retryable": True,
            **summary_metadata,
            "action": "Inspect the Douyin result page and connector mapping before retrying this source.",
        }

    if (
        "rate limit" in normalized
        or "too many requests" in normalized
        or re.search(r"\b429\b", normalized)
    ):
        return {
            "category": "rate_limited",
            "reason": "platform_rate_limited",
            "failure_stage": "platform_response",
            "severity": "warning",
            "retryable": True,
            **summary_metadata,
            "action": "Wait for the platform cooldown before retrying.",
        }
    if "opencli executable not found" in normalized or "no such file or directory" in normalized:
        return {
            "category": "missing_dependency",
            "failure_stage": "command_start",
            "severity": "blocked",
            "retryable": False,
            **summary_metadata,
            "action": "Install or configure the OpenCLI executable before running this source.",
        }
    if "timed out" in normalized or "timeout" in normalized:
        return {
            "category": "timeout",
            "failure_stage": "platform_request",
            "severity": "warning",
            "retryable": True,
            **summary_metadata,
            "action": "Retry later; if this repeats, increase timeout or inspect connector latency.",
        }
    reddit_unexpected_json = platform == "reddit" and (
        "unexpected token" in normalized or "unexpected json" in normalized
    )
    if (
        "invalid json" in normalized
        or "did not return valid json" in normalized
        or "not valid json" in normalized
        or reddit_unexpected_json
    ):
        return {
            "category": "invalid_output",
            "failure_stage": "response_parse",
            "severity": "error",
            "retryable": False,
            **summary_metadata,
            "action": "Inspect connector output mapping before retrying.",
        }
    if "ssl" in normalized or "connection" in normalized or "network" in normalized or "feed timeout" in normalized:
        return {
            "category": "network",
            "failure_stage": "platform_request",
            "severity": "warning",
            "retryable": True,
            **summary_metadata,
            "action": "Retry later or check the feed URL.",
        }
    if "opencli command failed with exit code" in normalized or "code: command_exec" in normalized:
        return {
            "category": "command_execution",
            "reason": "opencli_process_failed",
            "failure_stage": "command_execution",
            "severity": "error",
            "retryable": True,
            **summary_metadata,
            "action": "Inspect the OpenCLI exit code and preserved command output before retrying.",
        }
    return {
        "category": "connector_error",
        "failure_stage": "connector",
        "severity": "error",
        "retryable": platform in {"rss"},
        **summary_metadata,
        "action": "Inspect the connector error and source configuration.",
    }


def _auth_required_action(platform: str | None) -> str:
    labels = {
        "x": "X",
        "reddit": "Reddit",
        "youtube": "YouTube",
    }
    label = labels.get(platform or "", platform or "the platform")
    return f"Log in to {label} in the dedicated InfoOS browser profile, then rerun this source."


def _bounded_summary(value: str) -> str:
    if len(value) <= _SUMMARY_LIMIT:
        return value
    remaining = _SUMMARY_LIMIT - len(_SUMMARY_TRUNCATION_MARKER)
    head_length = remaining // 3
    tail_length = remaining - head_length
    return f"{value[:head_length]}{_SUMMARY_TRUNCATION_MARKER}{value[-tail_length:]}"
