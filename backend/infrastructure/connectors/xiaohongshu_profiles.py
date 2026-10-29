"""Bounded, independent profile batches after note text and first-screen comments."""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any, Callable

from backend.domain.sources.xiaohongshu_prefilter import apply_profile
from backend.infrastructure.security.redaction import redact_text


def _failure(exc: Exception, users: list[str]) -> tuple[str, str]:
    chain, current = [], exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    message = "; ".join(f"{type(error).__name__}: {error} {getattr(error, 'stderr_summary', '')}"
                        for error in chain)
    timeout = any(isinstance(error, (TimeoutError, subprocess.TimeoutExpired)) for error in chain)
    timeout = timeout or bool(re.search(r"(?i)\b(?:timed?\s*out|timeout)\b", message))
    # Sanitize before truncating, including command argv and OpenCLI stderr diagnostics.
    message = re.sub(r"(https?://[^\s\"'<>?#]+)[?#][^\s\"'<>]*", r"\1", message)
    message = re.sub(r"(?i)\bBearer\s+[^\s\"',;}]+", "[REDACTED]", message)
    message = re.sub(
        r'''(?i)["']?(?:[\w-]*token[\w-]*|authorization)["']?\s*(?:[:=,]\s*|\s+)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''',
        "[REDACTED]", message)
    for user in sorted(users, key=len, reverse=True):
        message = message.replace(user, "[AUTHOR]")
    message = re.sub(r"(/user/profile/)[a-zA-Z0-9]+", r"\1[AUTHOR]", message)
    message = " ".join(redact_text(message).split())
    return ("batch_timeout" if timeout else "batch_failed", message[:300])


def enrich_profiles(records: list[dict[str, Any]], rules: dict[str, Any],
                    load: Callable[..., Any]) -> list[dict[str, Any]]:
    users = list(dict.fromkeys(
        row["author_user_id"] for row in records
        if isinstance(row.get("author_user_id"), str)
        and re.fullmatch(r"[a-zA-Z0-9]{8,32}", row["author_user_id"])
    ))
    targets = list(dict.fromkeys(
        row["author_user_id"] for row in records if row["prefilter"]["status"] != "matched"
        and row.get("author_user_id") in users
    ))[:min(20, rules["max_profiles"])]
    profiles = {}
    for offset in range(0, len(targets), 5):
        if offset:
            time.sleep(max(3, rules["profile_gap_seconds"]))
        batch = targets[offset:offset + 5]
        results = {user: {"profile_ok": False, "profile_checked": None, "error": "batch_failed",
                          "profile_error_detail": "MissingProfileRow: adapter returned no result"}
                   for user in batch}
        batch_error = None
        risk = False
        try:
            payload = load(["xiaohongshu", "screening-profiles", json.dumps(batch)])
            if not isinstance(payload, list):
                raise ValueError("screening-profiles returned a non-list payload")
            risk = any(isinstance(row, dict) and row.get("error") == "login_or_risk" for row in payload)
            results.update({row["user_id"]: row for row in payload
                            if isinstance(row, dict) and row.get("user_id") in batch})
            if risk:
                batch_error = "login_or_risk"
            elif any(row.get("error") or row.get("profile_ok") is not True
                     or row.get("pinned_ok") is not True for row in results.values()):
                batch_error = "batch_failed"
        except Exception as exc:  # noqa: BLE001 - OpenCLI profile subprocess failures affect only this batch; do not retry
            batch_error, detail = _failure(exc, users)
            results = {user: {"profile_ok": False, "profile_checked": None, "error": batch_error,
                              "profile_error_detail": detail} for user in batch}
        profiles.update({user: {**row, "profile_batch_index": offset // 5 + 1,
                                "profile_batch_error": batch_error} for user, row in results.items()})
        if risk:
            profiles.update({user: {"profile_ok": False, "profile_checked": False, "error": "login_or_risk"}
                             for user in targets[offset + 5:]})
            break
    return [apply_profile(row, profiles.get(row.get("author_user_id")), rules) for row in records]
