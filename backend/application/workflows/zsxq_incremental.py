"""Derive a scheduled list window from durably stored source observations."""
from datetime import timedelta
from pathlib import Path

from backend.domain.sources.zsxq_request_budget import zsxq_command_timeout
from backend.domain.timestamps import format_utc_z, parse_utc
from backend.infrastructure.connectors.zsxq_runtime import incremental_max_pages
from backend.infrastructure.storage.raw_items import RawItemRepository


def apply_incremental_window(base_path: Path, source: dict, config: dict, task) -> None:
    command = config.get("command", [])
    if (source.get("platform") != "zsxq" or not task.artifacts_summary.get("scheduled_for")
            or task.artifacts_summary.get("initial_fetch") or task.artifacts_summary.get("digest_backfill_window")
            or source.get("source_type") not in {"topics", "opportunity"}
            or str(source.get("identifier") or "all") != "all"
            or command[:2] not in (["zsxq", "topics"], ["zsxq", "opportunity"])):
        return
    latest = RawItemRepository(base_path).latest_published_at(str(source["source_id"]))
    if latest is None:
        return
    since = format_utc_z(parse_utc(latest) - timedelta(hours=1))
    config["command"] = [*command, "--since", since]
    config["timeout_seconds"] = zsxq_command_timeout(config["command"], incremental_max_pages=incremental_max_pages())
