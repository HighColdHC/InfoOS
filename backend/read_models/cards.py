"""Source display metadata shared by current card readers."""
from __future__ import annotations

from pathlib import Path
from typing import TypedDict


class SourceDisplayInput(TypedDict):
    source_id: str | None
    source_platform: str
    source_author: str | None


class SourceDisplayMetadata(TypedDict):
    source_author: str | None
    source_name: str
    source_platform_label: str


def platform_display_label(platform: str) -> str:
    value = platform.strip()
    if not value:
        return "Unknown"
    try:
        from backend.domain.platforms import platform_capability

        return platform_capability(value).label
    except KeyError:
        if value.lower() == "rss":
            return "RSS"
        return value


def source_display_metadata(base_dir: Path | str, card: SourceDisplayInput) -> SourceDisplayMetadata:
    source = None
    if card["source_id"]:
        from backend.infrastructure.storage.repositories import SourceRepository

        source = SourceRepository(Path(base_dir)).get_source(card["source_id"])
    platform = card["source_platform"]
    source_author = card["source_author"]
    source_name = (
        (source["display_name"] if source else None)
        or source_author
        or card["source_id"]
        or platform_display_label(platform)
    )
    return {
        "source_author": source_author,
        "source_name": source_name,
        "source_platform_label": platform_display_label(platform),
    }
