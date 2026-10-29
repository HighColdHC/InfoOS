"""Small SQL predicate helpers for Source Run list queries."""

from __future__ import annotations

from typing import Any


def apply_list_filters(
    filters: list[str],
    params: list[Any],
    *,
    query: str | None,
    platform: str | None,
) -> None:
    normalized_platform = str(platform or "").strip()
    if normalized_platform:
        filters.append("platform = ?")
        params.append(normalized_platform)
    normalized_query = str(query or "").strip().casefold()
    if normalized_query:
        filters.append(
            "(lower(coalesce(display_name, '')) like ? "
            "or lower(coalesce(source_id, '')) like ? "
            "or lower(coalesce(stable_id, '')) like ? "
            "or lower(coalesce(artifacts_summary, '')) like ?)"
        )
        term = f"%{normalized_query}%"
        params.extend((term, term, term, term))
