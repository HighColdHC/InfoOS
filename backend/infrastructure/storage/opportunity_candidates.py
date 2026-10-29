"""Resolve real raw identities and seek the immutable raw entry-time index."""
import json
import sqlite3
from typing import Any, NotRequired, TypedDict, cast

from backend.domain.intelligence.opportunity import CandidateKind, OPPORTUNITY_LENS_VERSION
from backend.infrastructure.storage.base import InfoOSRepository


class PrefilterHit(TypedDict):
    field: str
    token: str


class PrefilterSummary(TypedDict):
    status: object
    rules_version: object
    hits: list[PrefilterHit]


class OpportunityCandidateRow(TypedDict):
    candidate_kind: CandidateKind
    card_id: str
    title: str
    platform: str
    source_type: str
    first_seen_at: str
    prefilter_summary: PrefilterSummary
    has_current_breakdown: bool
    is_digest: NotRequired[bool | None]


class OpportunityCandidateQueryRow(TypedDict):
    candidate_kind: CandidateKind
    card_id: str
    title: str
    platform: str
    source_type: str
    first_seen_at: str
    prefilter: NotRequired[object]
    has_current_breakdown: int | bool
    prefilter_summary: NotRequired[PrefilterSummary]


class OpportunityContextRow(TypedDict):
    candidate_kind: CandidateKind | None
    raw_payload: object
    raw_item_revision: int
    platform: str | None
    source_type: str | None


# RawItemRepository serializes RawItem, whose prefilter lives in raw_metadata.
# Accept the direct payload form too, with the same precedence as domain rules.
PREFILTER = """case when json_type(r.raw_payload, '$.prefilter') is not null
    then json_extract(r.raw_payload, '$.prefilter')
    else json_extract(r.raw_payload, '$.raw_metadata.prefilter') end"""
PREFILTER_CANDIDATE = "f.prefilter_candidate=1"
WIND_VANE_CANDIDATE = "(s.platform='zsxq' and (s.source_type='opportunity' or f.wind_vane_tagged=1))"
CASE_CANDIDATE = """exists(select 1 from card_evaluations e where e.evaluation_id=(
    select latest.evaluation_id from card_evaluations latest where latest.card_id=c.card_id
    order by latest.created_at desc, latest.evaluation_id desc limit 1)
    and e.content_type='case' and e.passed_quality=1 and e.relevance>=60)"""
CANDIDATE = f"({PREFILTER_CANDIDATE} or {WIND_VANE_CANDIDATE} or {CASE_CANDIDATE})"
CANDIDATE_KIND = f"""case when {PREFILTER_CANDIDATE} then 'prefilter'
    when {WIND_VANE_CANDIDATE} then 'wind_vane' when {CASE_CANDIDATE} then 'case' end"""
ENTRY_JOIN = """raw_items r indexed by idx_raw_items_first_seen_source
    cross join cards c indexed by idx_cards_opportunity_raw
    on c.raw_connector_id=r.connector_id and c.raw_item_id=r.raw_item_id
    join raw_item_candidate_flags f on f.connector_id=r.connector_id and f.raw_item_id=r.raw_item_id
    left join sources s on s.source_id=r.source_id"""
CURRENT_BREAKDOWN = """exists(select 1 from opportunity_breakdowns b where b.card_id=c.card_id
    and b.breakdown_id=(select n.breakdown_id from opportunity_breakdowns n where n.card_id=c.card_id
        order by n.created_at desc, n.breakdown_id desc limit 1)
    and b.lens_version=? and b.profile_version=? and b.raw_item_revision=r.revision)"""


def candidate_query(*, since: str, until: str | None, after: tuple[str, str] | None,
                    only_unbroken: bool, limit: int, profile_version: int | None) -> tuple[str, list[Any]]:
    clauses, params = ["r.first_seen_at >= ?", CANDIDATE], [OPPORTUNITY_LENS_VERSION, profile_version, since]
    if until is not None:
        clauses.append("r.first_seen_at < ?")
        params.append(until)
    if after:
        clauses.extend(["r.first_seen_at >= ?", "(r.first_seen_at, c.card_id) > (?, ?)"])
        params.extend([after[0], *after])
    if only_unbroken:
        clauses.append("not " + CURRENT_BREAKDOWN)
        params.extend([OPPORTUNITY_LENS_VERSION, profile_version])
    sql = f"""select c.card_id, c.title, c.source_platform as platform,
        coalesce(s.source_type, r.source_type) as source_type, r.first_seen_at,
        {PREFILTER} as prefilter, {CURRENT_BREAKDOWN} as has_current_breakdown,
        {CANDIDATE_KIND} as candidate_kind
        from {ENTRY_JOIN} where """ + " and ".join(clauses) + " order by r.first_seen_at, c.card_id limit ?"
    return sql, [*params, limit + 1]


class OpportunityCandidateRepository(InfoOSRepository):
    @staticmethod
    def context(conn: sqlite3.Connection, card_id: str) -> OpportunityContextRow | None:
        row = conn.execute(f"""select r.raw_payload, r.revision as raw_item_revision, s.platform, s.source_type,
            {CANDIDATE_KIND} as candidate_kind from cards c
            join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id
            join raw_item_candidate_flags f on f.connector_id=r.connector_id and f.raw_item_id=r.raw_item_id
            left join sources s on s.source_id=r.source_id where c.card_id=?""", (card_id,)).fetchone()
        return {**cast(OpportunityContextRow, dict(row)), "raw_payload": json.loads(row["raw_payload"])} if row else None

    def page(self, **kwargs) -> list[OpportunityCandidateRow]:
        sql, params = candidate_query(**kwargs)
        with self.connect_existing() as conn:
            rows = conn.execute(sql, params).fetchall()
        result = []
        for row in rows:
            item = cast(OpportunityCandidateQueryRow, dict(row))
            try:
                evidence = json.loads(item.pop("prefilter") or "null")
            except (ValueError, TypeError):
                evidence = None
            evidence = evidence if isinstance(evidence, dict) else {}
            # Only bounded screening summary, never full profiles or comments.
            hits = evidence.get("hits")
            item["prefilter_summary"] = {
                "status": evidence.get("status"), "rules_version": evidence.get("rules_version"),
                "hits": [{"field": str(hit.get("field", ""))[:100], "token": str(hit.get("token", ""))[:200]}
                         for hit in (hits if isinstance(hits, list) else [])[:10] if isinstance(hit, dict)],
            }
            item["has_current_breakdown"] = bool(item["has_current_breakdown"])
            result.append(item)
        return cast(list[OpportunityCandidateRow], result)
