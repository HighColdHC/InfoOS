"""Execution-lane selection for durable task workers."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Iterable

from backend.application.enrichment.external_doc_inputs import external_doc_inputs
from backend.application.tasks.types import Task, TaskType
from backend.application.tasks.queues import (
    CONTENT_ENRICH_BROWSER_PLATFORMS,
    MEDIA_DOWNLOAD_BROWSER_PLATFORMS,
)
from backend.domain.sources.configuration import database_source_to_connector_config
from backend.domain.sources.registry import source_browser_platforms
from backend.domain.external_doc_policy import content_enrich_needs_browser
from backend.infrastructure.storage.repositories import CardRepository, SourceRepository


_CONTENT_ENRICH_BROWSER_SITES = {
    platform: {"x": "twitter", "scys": "feishu", "zsxq": "feishu"}.get(platform, platform)
    for platform in CONTENT_ENRICH_BROWSER_PLATFORMS
}
_MEDIA_DOWNLOAD_BROWSER_SITES = {
    platform: {"x": "twitter"}.get(platform, platform)
    for platform in MEDIA_DOWNLOAD_BROWSER_PLATFORMS
}


class ExecutionLane(str, Enum):
    DEFAULT = "default"
    LOCAL_IO = "local_io"
    PUBLIC_ACQUISITION = "public_acquisition"
    BROWSER_ACQUISITION = "browser_acquisition"


def reachable_lanes_for(queue_names: Iterable[str] | None) -> set[ExecutionLane]:
    """Return lanes reachable under queue_name_for and the selection rules below.

    Acquisition queues retain DEFAULT for missing sources/cards and local
    enrich work. Unknown queues conservatively retain every lane.
    """
    all_lanes = set(ExecutionLane)
    if queue_names is None:
        return all_lanes
    queue_lanes = {
        # Source metadata may explicitly require a browser on a public platform.
        "public_fetch": {ExecutionLane.DEFAULT, ExecutionLane.PUBLIC_ACQUISITION,
                         ExecutionLane.BROWSER_ACQUISITION},
        "browser_fetch": {ExecutionLane.DEFAULT, ExecutionLane.BROWSER_ACQUISITION},
        "urgent_media": {ExecutionLane.DEFAULT},
        "card_pipeline": {ExecutionLane.DEFAULT},
        "gpu_asr": {ExecutionLane.DEFAULT},
        "cloud_ai": {ExecutionLane.DEFAULT},
        "maintenance_io": {ExecutionLane.DEFAULT, ExecutionLane.LOCAL_IO},
    }
    lanes: set[ExecutionLane] = set()
    for queue_name in queue_names:
        lanes.update(queue_lanes.get(queue_name, all_lanes))
    return lanes or all_lanes


def execution_lane_for(task: Task, base_dir: Path) -> ExecutionLane:
    """Return the execution resource lane required by one claimed task.

    Only source-targeted acquisition tasks need connector inspection.  An
    absent source deliberately stays on the default lane so normal task
    failure handling remains responsible for reporting the missing source.
    """
    return execution_lane_and_site_for(task, base_dir)[0]


def browser_resource_site_for(task: Task, base_dir: Path) -> str | None:
    return execution_lane_and_site_for(task, base_dir)[1]


def execution_lane_and_site_for(task: Task, base_dir: Path) -> tuple[ExecutionLane, str | None]:
    """Resolve lane and site, consulting raw evidence only for Feishu cards."""
    if task.task_type == TaskType.AUTH_QR_CAPTURE:
        return ExecutionLane.BROWSER_ACQUISITION, task.artifacts_summary["queue_platform"]
    if task.task_type == TaskType.DATA_EXPORT:
        return ExecutionLane.LOCAL_IO, None
    if task.artifacts_summary.get("capture_id") and task.task_type == TaskType.FETCH:
        if task.artifacts_summary.get("capture_adapter") in {"web", "github:item"}:
            return ExecutionLane.PUBLIC_ACQUISITION, None
        return ExecutionLane.BROWSER_ACQUISITION, _normalize_site(str(task.artifacts_summary.get("queue_platform") or ""))
    if task.task_type in {TaskType.SOURCE_CALIBRATE, TaskType.FETCH} and task.target_kind == "source":
        source = SourceRepository(base_dir).get_source(task.target_id)
        if source is None:
            return ExecutionLane.DEFAULT, None
        connector = database_source_to_connector_config(source)
        needs_browser = execution_requires_browser(connector, source)
        site = None
        if needs_browser:
            command = connector.get("command")
            if isinstance(command, list) and command:
                site = _normalize_site(str(command[0]))
            else:
                site = _normalize_site(str(connector.get("source_platform") or source.get("platform") or ""))
        if site is not None:
            return ExecutionLane.BROWSER_ACQUISITION, site
        if connector.get("type") in {"rss", "http", "feed"}:
            return ExecutionLane.PUBLIC_ACQUISITION, None
        if needs_browser:
            return ExecutionLane.BROWSER_ACQUISITION, None
        lane = ExecutionLane.PUBLIC_ACQUISITION if connector.get("type") == "opencli" else ExecutionLane.DEFAULT
        return lane, None
    if task.target_kind != "card" or task.task_type not in {TaskType.CONTENT_ENRICH, TaskType.MEDIA_DOWNLOAD}:
        return ExecutionLane.DEFAULT, None
    platform = str(CardRepository(base_dir).get_card_platform(task.target_id) or "").strip().lower()
    if task.task_type == TaskType.CONTENT_ENRICH and platform in {"zsxq", "scys"}:
        force_browser = bool(task.artifacts_summary.get("feishu_backfill")
                             or task.artifacts_summary.get("external_doc_retry"))
        queue_platform = str(task.artifacts_summary.get("queue_platform") or "").strip().lower()
        # Enqueue already ran the shared policy; reuse it instead of re-reading large raw payloads.
        if queue_platform and not force_browser:
            if queue_platform == "local":
                return ExecutionLane.DEFAULT, None
        else:
            card = CardRepository(base_dir).get_card(task.target_id)
            if card is None:
                return ExecutionLane.DEFAULT, None
            if not content_enrich_needs_browser(*external_doc_inputs(card, base_dir), force_browser=force_browser):
                return ExecutionLane.DEFAULT, None
    if task.task_type == TaskType.CONTENT_ENRICH and platform == "zsxq":
        from backend.domain.zsxq_article import article_gap

        card = CardRepository(base_dir).get_card(task.target_id)
        if card is not None and article_gap(external_doc_inputs(card, base_dir)[0]):
            return ExecutionLane.BROWSER_ACQUISITION, "zsxq"
    mapping = _CONTENT_ENRICH_BROWSER_SITES if task.task_type == TaskType.CONTENT_ENRICH else _MEDIA_DOWNLOAD_BROWSER_SITES
    site = mapping.get(platform)
    return (ExecutionLane.BROWSER_ACQUISITION if site is not None else ExecutionLane.DEFAULT), site


def execution_requires_browser(connector: dict, source: dict) -> bool:
    return (
        bool(connector.get("use_browser_window"))
        or bool(connector.get("requires_browser_session"))
        or str(connector.get("source_platform") or source.get("platform") or "") in source_browser_platforms()
    )


def _normalize_site(site: str) -> str | None:
    value = site.strip().lower()
    if not value:
        return None
    return {"x": "twitter", "xhs": "xiaohongshu", "rednote": "xiaohongshu"}.get(value, value)
