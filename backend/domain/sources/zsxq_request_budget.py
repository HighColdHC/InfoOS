"""Bounded ZSXQ command budget, matching adapter pagination and natural cooling."""
from __future__ import annotations

import json
import math


def zsxq_command_timeout(
    command: list[str], *, interval_ms: float = 1500, jitter_ms: float = 1000,
    retries: int = 1, cooldown_ms: float = 20000, incremental_max_pages: int = 5,
) -> int:
    def option(name: str, default: str) -> str:
        return command[command.index(name) + 1] if name in command else default

    limit = max(1, int(option("--limit", "20")))
    kind = command[1] if len(command) > 1 else "topics"
    if kind == "probe":
        return math.ceil(15 + (max(0, interval_ms) + max(0, jitter_ms)) / 1000 + 60)
    if kind == "search":
        keywords = json.loads(option("--keywords-json", "[]"))
        listed = option("--content-type", "all") == "essence" or "*" in keywords or command[2:3] == ["*"]
        pages = 40 if listed else math.ceil(limit / 30) + min(len(keywords) or 1, limit) - 1
        requests = pages + 2  # auth and optional group-name lookup
    elif kind in {"topics", "opportunity"}:
        requests = min(100, max(1, incremental_max_pages)) + 1 if "--since" in command else 101
    else:
        requests = 4  # whoami/topic metadata and comments
    retries = min(2, max(0, retries))
    cooldown_ms = min(60000, max(0, cooldown_ms))
    extra_requests = requests * retries
    if cooldown_ms:
        extra_requests = min(extra_requests, math.floor(60000 / cooldown_ms))
    spacing = max(0, interval_ms) + max(0, jitter_ms)
    # Each XHR is capped at 15s; command-wide 1059 waits total at most 60s.
    api_seconds = (requests + extra_requests) * (15 + spacing / 1000)
    hydration = min(540, max(40, limit * 40)) if option("--hydrate", "true") != "false" else 0
    return math.ceil(api_seconds + 60 + hydration + 60)  # navigation/output margin
