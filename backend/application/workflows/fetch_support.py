"""Stable connector-building helpers shared by fetch workflows."""
from __future__ import annotations

from pathlib import Path

from backend.application.usecases.source_config import SOURCE_METADATA_KEYS
from backend.domain.models.raw_item import RawItem
from backend.domain.sources.registry import research_default_sort
from backend.infrastructure.connectors import (
    DouyinCreatorConnector,
    DouyinItemConnector,
    OpenCLIConnector,
    ResearchConnector,
    XFixtureConnector,
)
from backend.infrastructure.connectors.feed.rss import RSSConnector
from backend.infrastructure.storage.paths import resolve_data_dir
from backend.infrastructure.connectors.x_timeline import XTimelineConnector


def annotate_raw_items(raw_items: list[RawItem], source: dict) -> list[RawItem]:
    source_metadata = {
        key: list(value) if isinstance(value, list) else value
        for key in SOURCE_METADATA_KEYS
        if (value := source.get(key)) is not None
    }
    if not source_metadata:
        return raw_items
    return [
        raw_item.model_copy(
            update={"raw_metadata": {**raw_item.raw_metadata, **source_metadata}}
        )
        for raw_item in raw_items
    ]


def build_connector(base_dir: Path, source: dict):
    connector_type = source["type"]
    if connector_type == "research":
        return ResearchConnector(
            connector_id=source["connector_id"],
            platform=source["research_platform"],
            source_id=source["source_id"],
            research_batch_id=source["research_batch_id"],
            keywords=list(source.get("keywords") or []),
            date_range=source.get("date_range"),
            max_items=int(source.get("max_items_per_run") or 20),
            sort=str(source.get("sort") or research_default_sort(source["research_platform"])),
            options=dict(source.get("options") or {}),
            rss_sources=list(source.get("rss_sources") or []),
            data_dir=resolve_data_dir(base_dir),
        )
    if connector_type == "rss":
        return RSSConnector(
            connector_id=source["connector_id"],
            feed_url=source["feed_url"],
            data_dir=base_dir / "data",
            source_platform=source.get("source_platform", "rss"),
            source_type=source.get("source_type", "article"),
            max_items_per_run=source.get("max_items_per_run"),
            feed_provider=source.get("feed_provider", "generic"),
            feed_content_mode=source.get("feed_content_mode", "feed_preferred"),
        )
    if connector_type == "x_fixture":
        fixture_path = Path(source["fixture_path"])
        if not fixture_path.is_absolute():
            fixture_path = base_dir / fixture_path
        return XFixtureConnector(connector_id=source["connector_id"], fixture_path=fixture_path)
    if connector_type == "opencli":
        connector_class = OpenCLIConnector
        if source.get("source_platform") == "x" and source.get("result_order") == "newest_first":
            connector_class = XTimelineConnector
        extra = {}
        if "digest_recheck" in source or "digest_backfill_window" in source:
            from backend.infrastructure.connectors.zsxq_topics import ZsxqTopicsConnector
            connector_class = ZsxqTopicsConnector
            extra = {"base_dir": base_dir, "digest_recheck": source.get("digest_recheck", {}),
                     "digest_group_id": source.get("digest_group_id", "1824528822"),
                     "digest_backfill_window": source.get("digest_backfill_window")}
        return connector_class(
            **extra,
            data_dir=resolve_data_dir(base_dir),
            connector_id=source["connector_id"],
            command=source["command"],
            executable=source.get("executable", "opencli"),
            source_platform=source.get("source_platform", "opencli"),
            source_type=source.get("source_type", "external_record"),
            items_path=source.get("items_path"),
            field_mapping=source.get("field_mapping", {}),
            metadata_mapping=source.get("metadata_mapping", {}),
            timeout_seconds=source.get("timeout_seconds", 60),
            result_order=source.get("result_order", "oldest_first"),
            use_browser_window=bool(source.get("use_browser_window", True)),
            detail_command_template=source.get("detail_command_template"),
            detail_max_items=source.get("detail_max_items"),
            xhs_prefilter_rules=source.get("xhs_prefilter_rules"),
        )
    if connector_type == "douyin_creator":
        return DouyinCreatorConnector(
            connector_id=source["connector_id"],
            command=source["command"],
            executable=source.get("executable", "opencli"),
            source_platform="douyin",
            source_type="creator",
            field_mapping=source.get("field_mapping", {}),
            metadata_mapping=source.get("metadata_mapping", {}),
            timeout_seconds=source.get("timeout_seconds", 60),
            result_order="newest_first",
            use_browser_window=True,
        )
    if connector_type == "douyin_item":
        return DouyinItemConnector(
            connector_id=source["connector_id"],
            identifier=source["identifier"],
            timeout_seconds=source.get("timeout_seconds", 120),
        )
    raise ValueError(f"unsupported source type: {connector_type}")
