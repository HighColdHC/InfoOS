"""Read-only source maintenance projections built from persisted runtime facts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple, TypedDict

from backend.domain.calibration.matrix import REQUIRED_LOGIN
from backend.domain.json_types import JsonObject
from backend.domain.sources.acquisition import AcquisitionArtifacts, acquisition_artifacts
from backend.domain.sources.capability import resolve_source_capability
from backend.domain.sources.configuration import DEFAULT_MAX_ITEMS_PER_RUN
from backend.domain.sources.zsxq import opportunity_source_for_topics
from backend.infrastructure.storage.repositories import SourceRepository
from backend.infrastructure.storage.sources import SourceRow
from backend.read_models._connections import shared_read_connection
from backend.read_models.source_cadence import CadenceSuggestion, suggest_source_cadences
from backend.read_models.source_facts import LatestRunRow, RecentRunCounts
from backend.read_models.source_health import SourceReadiness
from backend.read_models.source_management_page import (
    SourcePageFacts,
    SourceVersionRow,
    load_latest_source_versions,
    load_source_page_facts,
)


_FILTER_STATUSES = {"healthy", "needs_attention", "disabled"}


class SourceItem(TypedDict):
    source_id: str
    display_name: str
    platform: str
    source_type: str
    identifier: str
    health_status: str
    health_label: str
    status_explanation: str
    cadence_hours: float
    max_items_per_run: int
    auto_enrich_media: bool
    raw_metadata: JsonObject
    enabled: bool
    latest_run_at: str | None
    latest_run_status: str | None
    readiness_status: str
    login_status: str | None
    connector_id: str | None
    notes: str | None
    next_run_at: str | None
    generated_card_count: int
    current_config_version: str | None
    config_effective_at: str


class SourcePageSummary(TypedDict):
    total_sources: int
    enabled_sources: int
    needs_attention: int
    reliability_7d: float


class SourcePage(TypedDict):
    summary: SourcePageSummary
    items: list[SourceItem]
    total: int
    limit: int
    offset: int
    cadence_suggestions: dict[str, CadenceSuggestion]


class RecentRunStats(AcquisitionArtifacts):
    total: int
    success: int
    failed: int
    cards_created: int
    last_run_at: str | None
    last_status: str | None


class SourceDetail(TypedDict):
    source: SourceItem
    cadence_suggestion: CadenceSuggestion
    status_explanation: str
    current_config_version: str
    config_effective_at: str
    recent_run_stats: RecentRunStats


class _Listing(TypedDict):
    """Fields shared by list candidates and full items for filtering and the summary."""

    source_id: str
    display_name: str
    identifier: str
    connector_id: str | None
    platform: str
    enabled: bool
    health_status: str


class _Candidate(_Listing):
    source: SourceRow


class _SourceState(NamedTuple):
    readiness: SourceReadiness
    latest_run: LatestRunRow | None
    login_status: str | None
    login_required: bool
    health_status: str


@shared_read_connection
def read_source_page(
    base_dir: Path | str,
    *,
    query: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    enabled: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> SourcePage:
    base_path = Path(base_dir)
    sources = [
        source for source in SourceRepository(base_path).list_sources()
        if not source["raw_metadata"].get("system_managed")
    ]
    facts = load_source_page_facts(base_path, sources)
    candidates: list[_Candidate] = [
        {
            "source": source,
            "source_id": source["source_id"],
            "display_name": source["display_name"] or source["source_id"],
            "identifier": source["identifier"],
            "connector_id": source["connector_id"],
            "platform": source["platform"],
            "enabled": source["enabled"],
            "health_status": _source_state(source, facts).health_status,
        }
        for source in sources
    ]
    summary = _summary(candidates, facts.recent_run_counts)

    normalized_query = (query or "").strip().casefold()
    if normalized_query:
        candidates = [item for item in candidates if _matches_query(item, normalized_query)]
    normalized_platform = (platform or "").strip()
    if normalized_platform:
        candidates = [item for item in candidates if item["platform"] == normalized_platform]
    normalized_status = (status or "").strip()
    if normalized_status:
        candidates = [item for item in candidates if item["health_status"] == normalized_status]
    if enabled is not None:
        candidates = [item for item in candidates if item["enabled"] == enabled]
    candidates.sort(key=lambda item: (item["display_name"].casefold(), item["source_id"]))

    bounded_limit = max(1, min(int(limit), 100))
    bounded_offset = max(0, int(offset))
    page_sources = [item["source"] for item in candidates[bounded_offset : bounded_offset + bounded_limit]]
    versions = load_latest_source_versions(base_path, [source["source_id"] for source in page_sources])
    page_items = [
        _source_item(
            source, facts, versions=versions,
            opportunity_source_id=opportunity_source_for_topics(source, sources),
        )
        for source in page_sources
    ]
    return {
        "summary": summary,
        "items": page_items,
        "total": len(candidates),
        "limit": bounded_limit,
        "offset": bounded_offset,
        "cadence_suggestions": suggest_source_cadences(base_path, page_sources),
    }


def read_source_item(base_dir: Path | str, source_id: str) -> SourceItem | None:
    projection = _read_source_projection(Path(base_dir), source_id)
    return projection[1] if projection else None


@shared_read_connection
def read_source_detail(base_dir: Path | str, source_id: str) -> SourceDetail | None:
    base_path = Path(base_dir)
    projection = _read_source_projection(base_path, source_id)
    if projection is None:
        return None
    source, item, latest_run, recent_counts = projection
    return {
        "source": item,
        "cadence_suggestion": suggest_source_cadences(base_path, [source])[source_id],
        "status_explanation": item["status_explanation"],
        "current_config_version": item["current_config_version"] or "",
        "config_effective_at": item["config_effective_at"],
        "recent_run_stats": _recent_run_stats(latest_run, item["generated_card_count"], recent_counts),
    }


def valid_source_filter_status(value: str) -> bool:
    return value in _FILTER_STATUSES


def _read_source_projection(
    base_dir: Path, source_id: str,
) -> tuple[SourceRow, SourceItem, LatestRunRow | None, RecentRunCounts] | None:
    repo = SourceRepository(base_dir)
    source = repo.get_maintained_source(source_id)
    if source is None or source["raw_metadata"].get("system_managed"):
        return None
    facts = load_source_page_facts(base_dir, [source], source_ids=[source_id])
    opportunity_id = None
    if source["platform"] == "zsxq" and source["source_type"] == "topics":
        # Ownership needs peer configuration only, never peer runs/cards/tasks.
        peers = [row for row in repo.list_sources() if not row["raw_metadata"].get("system_managed")]
        opportunity_id = opportunity_source_for_topics(source, peers)
    item = _source_item(
        source, facts,
        versions=load_latest_source_versions(base_dir, [source_id]),
        opportunity_source_id=opportunity_id,
    )
    recent_counts = facts.recent_run_counts.get(source_id) or RecentRunCounts(total=0, success=0, failed=0)
    return source, item, facts.latest_runs.get(source_id), recent_counts


def _source_state(source: SourceRow, facts: SourcePageFacts) -> _SourceState:
    source_id = source["source_id"]
    readiness = facts.readiness[source_id]
    latest_run = facts.latest_runs.get(source_id)
    platform = source["platform"]
    login_status = facts.login_by_platform.get(platform)
    if _passive_auth_block_is_superseded(platform, login_status, readiness, latest_run):
        readiness = SourceReadiness(status="ready", issues=readiness["issues"], diagnostic=readiness["diagnostic"])
    login_required = (
        resolve_source_capability(source).requires_login == REQUIRED_LOGIN
        and login_status in {"needs_login", "failed"}
        and not _successful_run_is_authoritative_login_evidence(
            platform, readiness, latest_run,
        )
    )
    return _SourceState(
        readiness, latest_run, login_status, login_required,
        _health_status(source, readiness, latest_run, login_required=login_required),
    )


def _source_item(
    source: SourceRow,
    facts: SourcePageFacts,
    *,
    versions: dict[str, SourceVersionRow],
    opportunity_source_id: str | None = None,
) -> SourceItem:
    source_id = source["source_id"]
    state = _source_state(source, facts)
    latest_run = state.latest_run
    platform = source["platform"]
    version = versions.get(source_id)
    raw_metadata = source["raw_metadata"]
    max_items = raw_metadata.get("max_items_per_run")
    if max_items is None:
        max_items = DEFAULT_MAX_ITEMS_PER_RUN.get((platform, source["source_type"]), 20)
    explanation = _status_explanation(
        source, state.readiness, latest_run, login_required=state.login_required,
    )
    if opportunity_source_id:
        explanation += f"；风向标帖由 {opportunity_source_id} 负责。"
    item: SourceItem = {
        "source_id": source_id,
        "display_name": source["display_name"] or source_id,
        "platform": platform,
        "source_type": source["source_type"],
        "identifier": source["identifier"],
        "health_status": state.health_status,
        "health_label": _health_label(state.health_status),
        "status_explanation": explanation,
        "cadence_hours": source["cadence_hours"],
        "max_items_per_run": int(max_items or 20),
        "auto_enrich_media": _boolean(raw_metadata.get("auto_enrich_media"), default=True),
        "raw_metadata": dict(raw_metadata),
        "enabled": source["enabled"],
        "latest_run_at": _run_time(latest_run),
        "latest_run_status": latest_run["status"] if latest_run else None,
        "readiness_status": "login_required" if state.login_required else state.readiness["status"],
        "login_status": state.login_status,
        "connector_id": source["connector_id"],
        "notes": source["notes"],
        "next_run_at": (
            source["next_run_at"] or (latest_run["next_run_at"] if latest_run else None)
        ),
        "generated_card_count": facts.card_counts.get(source_id, 0),
        "current_config_version": version["config_version"] if version else None,
        "config_effective_at": version["created_at"] if version else source["updated_at"],
    }
    return item


def _successful_run_is_authoritative_login_evidence(
    platform: str,
    readiness: SourceReadiness,
    latest_run: LatestRunRow | None,
) -> bool:
    """SCYS has no reliable cookie-only login probe; its successful run is authoritative."""
    return bool(
        platform == "scys"
        and latest_run
        and latest_run["status"] == "success"
        and readiness["status"] == "ready"
    )


def _passive_auth_block_is_superseded(
    platform: str,
    login_status: str | None,
    readiness: SourceReadiness,
    latest_run: LatestRunRow | None,
) -> bool:
    """Keep the maintenance projection passive when stronger persisted evidence exists."""
    diagnostic = readiness["diagnostic"]
    return bool(
        latest_run
        and latest_run["status"] == "success"
        and diagnostic is not None
        and diagnostic["category"] == "auth_required"
        and (
            platform == "scys"
            or login_status not in {"needs_login", "failed"}
        )
    )


def _summary(
    items: Sequence[_Listing], recent_run_counts: Mapping[str, RecentRunCounts],
) -> SourcePageSummary:
    successes = sum(counts["success"] for counts in recent_run_counts.values())
    completed = sum(counts["success"] + counts["failed"] for counts in recent_run_counts.values())
    return {
        "total_sources": len(items),
        "enabled_sources": sum(1 for item in items if item["enabled"]),
        "needs_attention": sum(1 for item in items if item["health_status"] == "needs_attention"),
        "reliability_7d": successes / completed if completed else 0.0,
    }


def _health_status(
    source: SourceRow,
    readiness: SourceReadiness,
    latest_run: LatestRunRow | None,
    *,
    login_required: bool = False,
) -> str:
    if not source["enabled"]:
        return "disabled"
    if login_required:
        return "needs_attention"
    if latest_run and latest_run["status"] == "success" and readiness["status"] == "ready":
        return "healthy"
    return "needs_attention"


def _health_label(status: str) -> str:
    return {
        "healthy": "健康",
        "needs_attention": "需处理",
        "disabled": "已停用",
    }[status]


def _status_explanation(
    source: SourceRow,
    readiness: SourceReadiness,
    latest_run: LatestRunRow | None,
    *,
    login_required: bool = False,
) -> str:
    if not source["enabled"]:
        return "来源已停用，不再参与定时调度。"
    if login_required:
        return "平台账号需要登录，请前往采集环境维护，在专用 Chrome 中完成登录并检查状态。"
    if latest_run and latest_run["status"] == "success" and readiness["status"] == "ready":
        return "最近一次采集成功。"
    if latest_run and latest_run["status"] == "failed":
        return "最近一次采集失败，请查看运行追踪了解原因。"
    issues = readiness["issues"]
    if issues:
        return issues[0]
    return "尚无成功采集记录，需要完成首次采集。"


def _recent_run_stats(
    latest: LatestRunRow | None, cards_created: int, recent_counts: RecentRunCounts,
) -> RecentRunStats:
    raw_payload: JsonObject = json.loads(latest["raw_payload"]) if latest else {}
    return {
        **acquisition_artifacts(raw_payload.get("acquisition")),
        "total": recent_counts["total"],
        "success": recent_counts["success"],
        "failed": recent_counts["failed"],
        "cards_created": cards_created,
        "last_run_at": _run_time(latest),
        "last_status": latest["status"] if latest else None,
    }


def _matches_query(item: _Listing, query: str) -> bool:
    return query in "\n".join(
        (item["display_name"], item["identifier"], item["source_id"], item["connector_id"] or "")
    ).casefold()


def _run_time(run: LatestRunRow | None) -> str | None:
    if not run:
        return None
    return run["finished_at"] or run["started_at"] or run["scheduled_for"]


def _boolean(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)
