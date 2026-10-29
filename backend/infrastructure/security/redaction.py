from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Any


SECRET_KEY_PARTS = ("token", "cookie", "session", "authorization", "password", "secret")
REDACTED = "[REDACTED]"

_BEARER_PATTERN = re.compile(r"(?i)\b(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_INFOOS_TOKEN_PATTERN = re.compile(r"\bios_[A-Za-z0-9_-]{20,}\b")
_API_KEY_PATTERN = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b")
_HEADER_PATTERN = re.compile(
    r"(?im)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*[^\r\n]+"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|api_key|apikey|auth|authorization|key|signature|sig|token)=)[^&#\s]+"
)
_ALL_QUERY_VALUE_PATTERN = re.compile(r"([?&][^\s&#=]+)=([^\s&#]*)")
_NAMED_SECRET_PATTERN = re.compile(
    r'''(?i)(["']?(?:cookie|authorization|token|sessionid)["']?\s*[:=]\s*)["']?([^"'\s,;}]+)["']?'''
)
_EMAIL_PATTERN = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_PATTERN = re.compile(r"(?<![\w])\+?\d(?:[\s().-]*\d){6,14}(?![\w])")

DIAGNOSTIC_SUMMARY_MAX_CHARS = 800
_DIAGNOSTIC_TAIL_LINES = 12


def is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SECRET_KEY_PARTS)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            if is_secret_key(str(key)):
                if isinstance(child, (dict, list)):
                    redacted[key] = redact_secrets(child)
                else:
                    redacted[key] = REDACTED
            else:
                redacted[key] = redact_secrets(child)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(child) for child in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(child) for child in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    """Redact credentials and local roots embedded in otherwise safe text."""

    redacted = _HEADER_PATTERN.sub(REDACTED, value)
    redacted = _BEARER_PATTERN.sub(REDACTED, redacted)
    redacted = _INFOOS_TOKEN_PATTERN.sub(REDACTED, redacted)
    redacted = _API_KEY_PATTERN.sub(REDACTED, redacted)
    redacted = _QUERY_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    for root, label in _sensitive_roots():
        if root:
            redacted = redacted.replace(root, label)
    return redacted


def summarize_subprocess_output(
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    *,
    max_chars: int = DIAGNOSTIC_SUMMARY_MAX_CHARS,
    tail_lines: int = _DIAGNOSTIC_TAIL_LINES,
) -> str:
    """Return a bounded, redacted tail suitable for persisted diagnostics."""

    streams = (_coerce_output(stdout), _coerce_output(stderr))
    lines = [line.strip() for stream in streams for line in stream.splitlines() if line.strip()]
    summary = "\n".join(lines[-max(1, tail_lines):])
    summary = redact_text(summary)
    summary = _ALL_QUERY_VALUE_PATTERN.sub(lambda match: f"{match.group(1)}={REDACTED}", summary)
    summary = _NAMED_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{REDACTED}", summary)
    summary = _EMAIL_PATTERN.sub(REDACTED, summary)
    summary = _PHONE_PATTERN.sub(REDACTED, summary)
    if len(summary) <= max_chars:
        return summary
    return summary[-max_chars:]


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _sensitive_roots() -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for env_name, label in (
        ("INFOOS_STATE_HOME", "<INFOOS_STATE_HOME>"),
        ("INFOOS_DATA_HOME", "<INFOOS_DATA_HOME>"),
    ):
        raw = os.environ.get(env_name)
        if raw:
            values.append((str(Path(raw).expanduser()), label))
    home = str(Path.home())
    if home:
        values.append((home, "<USER_HOME>"))
    return tuple(sorted(values, key=lambda item: len(item[0]), reverse=True))
