"""Registry and persisted source audit projections for agents."""
from pathlib import Path
from typing import TypedDict, cast

from backend.domain.sources.registry import capability_catalog
from backend.domain.sources.registry_types import PlatformCapabilityItem
from backend.infrastructure.storage.source_change_audit import SourceChangeAuditRepository, SourceChangePage


class SourceCapabilities(TypedDict):
    platforms: list[PlatformCapabilityItem]


def read_source_capabilities() -> SourceCapabilities:
    return {"platforms": [
        cast(PlatformCapabilityItem, {key: value for key, value in item.items() if key != "research"})
        for item in capability_catalog() if item["configurable"]
    ]}


def read_source_changes(base_dir: Path, source_id: str | None = None,
                        *, limit: int = 50, offset: int = 0) -> SourceChangePage:
    return SourceChangeAuditRepository(base_dir).page(source_id, limit=limit, offset=offset)
