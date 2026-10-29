"""Read-only browse filter choices with the existing platform display labels."""
from datetime import datetime
from pathlib import Path

from backend.infrastructure.storage.card_browse_query import read_preview_facets
from backend.infrastructure.storage.card_browse_query_types import BrowseFacets
from backend.read_models.cards import platform_display_label


def read_card_preview_facets(
    base_dir: Path, *, published_since: datetime | None = None,
    published_until: datetime | None = None, scope: str = "all",
) -> BrowseFacets:
    result = read_preview_facets(base_dir, published_since=published_since,
                                 published_until=published_until, scope=scope)
    for platform in result["platforms"]:
        platform["label"] = platform_display_label(platform["platform"])
    return result
