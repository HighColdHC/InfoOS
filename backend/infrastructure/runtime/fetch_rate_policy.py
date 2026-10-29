"""Shared fetch policy resolution for reservations and circuit recovery."""
from __future__ import annotations

import json
import math
import os
from typing import Any

_PLATFORM_DEFAULTS: dict[str, dict[str, int]] = {
    "x": {
        "min_gap_seconds": 12,
        "jitter_seconds": 8,
        "hourly_budget": 120,
        "daily_budget": 800,
    },
    "youtube": {
        "min_gap_seconds": 8,
        "jitter_seconds": 4,
        "hourly_budget": 120,
        "daily_budget": 800,
    },
}
_X_SEARCH_DEFAULTS = {
    "hourly_budget": 20,
    "daily_budget": 200,
}


def resolve_fetch_policy(source: dict[str, Any]) -> dict[str, Any] | None:
    platform = str(source.get("platform") or "")
    x_search = platform == "x" and source.get("source_scope") == "research"
    policy_scope = "x_search" if x_search else platform
    raw_meta = _raw_metadata(source)
    configured = raw_meta.get("fetch_rate_policy")
    configured = configured if isinstance(configured, dict) else {}
    if configured.get("enabled") is False:
        return None
    estimated_pages = None
    if x_search:
        estimated_pages = _estimated_search_pages(raw_meta.get("max_items_per_run"))
        min_gap_seconds = estimated_pages * _positive_env_int("INFOOS_X_SEARCH_SECONDS_PER_PAGE", 60)
        defaults = {
            **_X_SEARCH_DEFAULTS,
            "min_gap_seconds": min_gap_seconds,
            "jitter_seconds": min_gap_seconds // 2,
        }
    else:
        defaults = _PLATFORM_DEFAULTS.get(platform)
    if defaults is None and not configured:
        return None
    defaults = defaults or {
        "min_gap_seconds": 0,
        "jitter_seconds": 0,
        "hourly_budget": 0,
        "daily_budget": 0,
    }
    identity_id = str(
        configured.get("identity_id")
        or raw_meta.get("fetch_identity_id")
        or raw_meta.get("identity_id")
        or f"{platform}_default"
    )
    return {
        "platform": platform,
        "identity_id": identity_id,
        "throttle_key": f"{platform}:{identity_id}" + (":search" if x_search else ""),
        "min_gap_seconds": _policy_int(policy_scope, configured, "min_gap_seconds", defaults["min_gap_seconds"]),
        "jitter_seconds": _policy_int(policy_scope, configured, "jitter_seconds", defaults["jitter_seconds"]),
        "hourly_budget": _policy_int(policy_scope, configured, "hourly_budget", defaults["hourly_budget"]),
        "daily_budget": _policy_int(policy_scope, configured, "daily_budget", defaults["daily_budget"]),
        **({"estimated_pages": estimated_pages} if x_search else {}),
    }


def _estimated_search_pages(max_items: Any) -> int:
    if isinstance(max_items, bool):
        return 5
    try:
        return max(1, math.ceil(float(max_items) / 20))
    except (TypeError, ValueError, OverflowError):
        return 5


def _positive_env_int(name: str, default: int) -> int:
    value = _int(os.environ.get(name), default)
    return value if value > 0 else default


def _policy_int(platform: str, configured: dict[str, Any], key: str, default: int) -> int:
    if key in configured:
        return max(0, _int(configured.get(key), default))
    env_name = f"INFOOS_{platform.upper()}_FETCH_{key.upper()}"
    return max(0, _int(os.environ.get(env_name), default))


def _raw_metadata(source: dict[str, Any]) -> dict[str, Any]:
    raw_meta = source.get("raw_metadata") or {}
    if isinstance(raw_meta, str):
        try:
            raw_meta = json.loads(raw_meta)
        except (TypeError, ValueError):
            raw_meta = {}
    return raw_meta if isinstance(raw_meta, dict) else {}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def recovery_fetch_gap(source: dict[str, Any]) -> int:
    policy = resolve_fetch_policy(source)
    if policy is None:
        return 60
    platform = str(source.get("platform") or "")
    configured = _raw_metadata(source).get("fetch_rate_policy") or {}
    configured_gap = isinstance(configured, dict) and "min_gap_seconds" in configured
    if not configured_gap and platform not in _PLATFORM_DEFAULTS and not os.environ.get(
        f"INFOOS_{platform.upper()}_FETCH_MIN_GAP_SECONDS"
    ):
        return 60
    return max(1, policy["min_gap_seconds"])
