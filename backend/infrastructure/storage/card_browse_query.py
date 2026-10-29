"""Indexed source statistics and standalone browse projections."""
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from backend.infrastructure.storage.cards import CardRepository
from backend.infrastructure.storage.card_preview_query import _LATEST_EVALUATION_JOIN, _published_key

from backend.infrastructure.storage.card_browse_query_types import (
    BrowseFacets, BrowsePlatform, BrowseSource, PreviewOpportunity,
)

SOURCE_LIMIT = 1000
PLATFORM_LIMIT = 100


def _facet_query(
    *, published_since: datetime | None = None,
    published_until: datetime | None = None, scope: str = "all",
) -> tuple[str, list[Any]]:
    # Keep the standalone browse surface's maintained-only choices explicit.
    where = """c.status='active' and c.version>=1 and c.content_hash is not null
        and length(trim(c.content_hash))>0"""
    if scope == "maintained":
        where += """ and s.source_scope='maintained'
            and coalesce(json_extract(s.raw_metadata, '$.system_managed'), 0)=0"""
    params: list[Any] = []
    for bound, operator in ((published_since, ">="), (published_until, "<=")):
        if bound is not None:
            where += f" and c.published_ts {operator} ?"
            params.append(_published_key(bound))
    return f"""select coalesce(s.source_id, '__none__') as source_id,
                     coalesce(s.display_name, '未关联来源') as name,
                     c.source_platform as platform, count(*) as card_count,
                     count(e.evaluation_id) as evaluated_count,
                     sum(case when e.passed_quality=1 then 1 else 0 end) as passed_count
              from cards c left join sources s on s.source_id=c.source_id
              left {_LATEST_EVALUATION_JOIN}
              where {where} group by s.source_id, c.source_platform
              order by card_count desc, name, source_id, platform""", params


def read_preview_facets(
    base_dir: Path, *, published_since: datetime | None = None,
    published_until: datetime | None = None, scope: str = "all",
) -> BrowseFacets:
    sql, params = _facet_query(published_since=published_since, published_until=published_until, scope=scope)
    with CardRepository(base_dir).connect_existing() as conn:
        # One aggregation, no card payloads or blocks; latest evaluation uses its
        # existing (card_id, created_at DESC, evaluation_id DESC) index.
        sources = [cast(BrowseSource, dict(row)) for row in conn.execute(sql, params)]
    counts: dict[str, int] = {}
    for source in sources:
        counts[source["platform"]] = counts.get(source["platform"], 0) + source["card_count"]
    platforms: list[BrowsePlatform] = [{"platform": key, "card_count": count} for key, count in
                 sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]
    # Management statistics are complete, including orphan source IDs per platform.
    bounded = scope == "maintained"
    return {"platforms": platforms[:PLATFORM_LIMIT] if bounded else platforms,
            "sources": sources[:SOURCE_LIMIT] if bounded else sources,
            "limits": {"platforms": PLATFORM_LIMIT if bounded else None,
                       "sources": SOURCE_LIMIT if bounded else None},
            "truncated": {"platforms": bounded and len(platforms) > PLATFORM_LIMIT,
                          "sources": bounded and len(sources) > SOURCE_LIMIT}}


def read_preview_opportunities(base_dir: Path, card_ids: list[str]) -> dict[str, PreviewOpportunity]:
    """One batch, latest breakdown by the same order as the opportunity repository."""
    if not card_ids:
        return {}
    placeholders = ",".join("?" for _ in card_ids)
    with CardRepository(base_dir).connect_existing() as conn:
        rows = conn.execute(
            f"""select b.card_id, b.next_step from opportunity_breakdowns b
                where b.card_id in ({placeholders}) and b.breakdown_id=(
                    select n.breakdown_id from opportunity_breakdowns n where n.card_id=b.card_id
                    order by n.created_at desc, n.breakdown_id desc limit 1)""", card_ids,
        ).fetchall()
    return {row["card_id"]: {"has_breakdown": True, "next_step": row["next_step"]} for row in rows}
