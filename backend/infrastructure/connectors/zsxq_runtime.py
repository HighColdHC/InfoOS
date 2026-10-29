"""Resolve adapter timing overrides into the subprocess/browser deadline."""
from __future__ import annotations

import math
import os
import re

from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout


def command_timeout(command: list[str]) -> int:
    def setting(name: str, fallback: float) -> float:
        try:
            value = float(os.environ.get(name, ""))
        except ValueError:
            return fallback
        return value if math.isfinite(value) and value >= 0 else fallback

    return zsxq_command_timeout(
        command,
        incremental_max_pages=incremental_max_pages(),
        interval_ms=setting("INFOOS_ZSXQ_REQUEST_INTERVAL_MS", 1500),
        jitter_ms=setting("INFOOS_ZSXQ_REQUEST_JITTER_MS", 1000),
        retries=int(setting("INFOOS_ZSXQ_1059_RETRIES", 1)),
        cooldown_ms=setting("INFOOS_ZSXQ_1059_COOLDOWN_MS", 20000),
    )


def incremental_max_pages() -> int:
    raw = os.environ.get("INFOOS_ZSXQ_INCREMENTAL_MAX_PAGES", "5").strip()
    if not re.fullmatch(r"[+]?[0-9]+", raw):
        return 5
    value = int(raw)
    return min(100, value) if value > 0 else 5


def run_probe(group_id: str) -> None:
    """One authenticated list request; retry policy is pinned by the probe adapter."""
    from backend.infrastructure.connectors.opencli import OpenCLIConnector
    command = ["zsxq", "probe", "--group_id", group_id]
    OpenCLIConnector(connector_id="zsxq:circuit_probe", source_platform="zsxq",
                     command=command, timeout_seconds=command_timeout(command)).fetch_new_items(None)
