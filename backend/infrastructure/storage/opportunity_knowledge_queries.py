"""Knowledge-backed opportunity board and statistics SQL projections."""
import json
from typing import TypedDict, cast

from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.knowledge_queries import search_terms

OPPORTUNITY = "k.kind='opportunity' and k.status!='superseded'"


class OpportunityBoardRow(TypedDict):
    knowledge_id: str
    title: str
    subtype: str
    use_note: str
    strength: int
    research_status: str
    status_changed_at: str
    created_at: str
    updated_at: str
    origin_freshness: str
    money_signal: str
    signal_kinds: list[str]
    evidence_count: int
    platforms: list[str]


class OpportunityBoardRows(TypedDict):
    totals: dict[str, int]
    items: list[OpportunityBoardRow]


class OpportunityStatsRows(TypedDict):
    status_subtype: list[tuple[str, str, int]]
    signals: dict[str, int]
    daily: dict[str, int]
    platforms: dict[str, int]


def _filters(status: str | None, subtype: str | None, platform: str | None, query: str) -> tuple[str, list[str]]:
    clauses, params = [OPPORTUNITY], []
    for value, clause in ((status, "k.research_status=?"), (subtype, "k.subtype=?"),
        (platform, "exists(select 1 from knowledge_evidence e join cards c on c.card_id=e.card_id "
         "where e.knowledge_id=k.knowledge_id and c.source_platform=?)")):
        if value is not None:
            clauses.append(clause)
            params.append(value)
    terms, words = search_terms(query, ("k.title", "k.use_note", "k.fields_json"))
    return " and ".join([*clauses, *terms]), [*params, *words]


class OpportunityKnowledgeQueryRepository(InfoOSRepository):
    def board(self, *, status: str | None, subtype: str | None, platform: str | None,
              query: str, limit: int, offset: int) -> OpportunityBoardRows:
        where, params = _filters(status, subtype, platform, query)
        with self.connect_existing() as conn:
            totals = {row["status"]: row["total"] for row in conn.execute(
                f"select k.research_status as status,count(*) as total from knowledge_cards k where {where} "
                "group by k.research_status", params)}
            rows = conn.execute(f"""with ranked as (
              select k.knowledge_id,k.title,k.subtype,k.use_note,k.strength,k.research_status,k.origin_freshness,
                k.created_at,k.updated_at,
                json_extract(k.fields_json,'$.money_signal') as money_signal,
                json_extract(k.fields_json,'$.signal_kinds') as signal_kinds,
                coalesce((select max(s.created_at) from knowledge_status_events s
                  where s.knowledge_id=k.knowledge_id and s.field='research_status'),k.created_at) as status_changed_at
              from knowledge_cards k where {where}),
            positioned as (
              select r.*,row_number() over (partition by r.research_status order by
                  case when r.research_status='pending' then r.strength end desc,
                  case when r.research_status='pending' then r.created_at else r.status_changed_at end desc,
                  r.knowledge_id desc) as position
              from ranked r)
            select p.knowledge_id,p.title,p.subtype,p.use_note,p.strength,p.research_status,p.status_changed_at,
              p.created_at,p.updated_at,p.origin_freshness,p.money_signal,p.signal_kinds,
              (select count(*) from knowledge_evidence e where e.knowledge_id=p.knowledge_id) as evidence_count,
              (select json_group_array(distinct c.source_platform) from knowledge_evidence e
                 join cards c on c.card_id=e.card_id where e.knowledge_id=p.knowledge_id) as platforms
            from positioned p where p.position>? and p.position<=?
            order by p.research_status,p.position""", [*params, offset, offset + limit]).fetchall()
        items = []
        for row in rows:
            item = cast(OpportunityBoardRow, dict(row))
            item["signal_kinds"] = json.loads(row["signal_kinds"])
            item["platforms"] = sorted(json.loads(row["platforms"]))
            items.append(item)
        return {"totals": totals, "items": items}

    def stats(self, *, since: str | None) -> OpportunityStatsRows:
        sw = OPPORTUNITY + (" and k.created_at>=?" if since is not None else "")
        params = [since] if since is not None else []
        with self.connect_existing() as conn:
            status_subtype = [(row[0], row[1], row[2]) for row in conn.execute(
                f"select k.research_status,k.subtype,count(*) as count from knowledge_cards k where {sw} "
                "group by k.research_status,k.subtype", params)]
            signals = dict(conn.execute("select j.value as signal,count(distinct k.knowledge_id) as count "
                f"from knowledge_cards k join json_each(k.fields_json,'$.signal_kinds') j where {sw} group by j.value", params))
            daily = dict(conn.execute("select date(k.created_at,'+8 hours') as date,count(*) as count "
                f"from knowledge_cards k where {sw} group by date(k.created_at,'+8 hours') order by date", params))
            platforms = dict(conn.execute("select c.source_platform as platform,count(distinct k.knowledge_id) as count "
                "from knowledge_cards k join knowledge_evidence e on e.knowledge_id=k.knowledge_id "
                f"join cards c on c.card_id=e.card_id where {sw} group by c.source_platform", params))
        return {"status_subtype": status_subtype, "signals": signals, "daily": daily, "platforms": platforms}
