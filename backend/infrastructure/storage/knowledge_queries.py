"""Read projections for knowledge evidence, candidates and library search."""
import json
import sqlite3
from typing import Any

from backend.domain.intelligence.knowledge import EXTRACTION_VERSION, deterministic_channel, freshness
from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.domain.intelligence.opportunity import prefilter_evidence
from backend.domain.intelligence.triage import TRIAGE_VERSION
from backend.domain.timestamps import utc_now_micro_z
from backend.infrastructure.storage.base import InfoOSRepository

COLUMNS = """knowledge_id, kind, subtype, title, fields_json, attribution_kind, attribution_ref,
use_note, strength, origin_published_at, origin_freshness, status, superseded_by, research_status,
extraction_version, profile_version, evaluator, created_at, updated_at"""
CONTEXT = """select c.card_id, c.version, c.title, c.source_platform, c.published_at, c.content,
    c.status, c.raw_payload, r.first_seen_at as entered_at,
    coalesce(s.source_type, r.source_type, c.source_type) as source_type,
    r.raw_payload as raw_item_payload, e.prompt_version, e.passed_quality, e.exclusion, e.extract_kinds,
    exists(select 1 from knowledge_extractions x where x.card_id=c.card_id and x.card_version=c.version
        and x.extraction_version=?) as extracted
    from cards c join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id
    left join sources s on s.source_id=r.source_id
    left join card_evaluations e on e.evaluation_id=(select n.evaluation_id from card_evaluations n
        where n.card_id=c.card_id order by n.created_at desc, n.evaluation_id desc limit 1)"""
ENTRY = "r.first_seen_at"


def decode(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    value["fields"] = json.loads(value.pop("fields_json"))
    return value


def search_terms(query: str, columns: tuple[str, ...]) -> tuple[list[str], list[str]]:
    clauses, params = [], []
    for word in query.split():
        clauses.append("(" + " or ".join(f"instr(lower({column}),lower(?))>0" for column in columns) + ")")
        params.extend([word] * len(columns))
    return clauses, params


def candidate(row: sqlite3.Row, at: str) -> dict[str, Any] | None:
    value = dict(row)
    value["raw_payload"] = json.loads(value["raw_payload"])
    raw = json.loads(value.pop("raw_item_payload")) if value["raw_item_payload"] is not None else {}
    channel = deterministic_channel(value, value["source_type"]) or deterministic_channel(
        {**value, "raw_payload": raw}, value["source_type"])
    kinds = json.loads(value["extract_kinds"]) if value["extract_kinds"] is not None else []
    eligible = channel is not None or (value["prompt_version"] == TRIAGE_VERSION and value["passed_quality"] == 1
        and value["exclusion"] is None and any(kind in KINDS and KINDS[kind].enabled for kind in kinds))
    if value["status"] != "active" or value["extracted"] or not eligible:
        return None
    return {"card_id": value["card_id"], "card_version": value["version"], "title": value["title"],
            "platform": value["source_platform"], "entered_at": value["entered_at"],
            "published_at": value["published_at"], "channel": "deterministic" if channel else "triage",
            "deterministic_channel": channel, "extract_kinds": kinds,
            "prefilter_status": prefilter_evidence(raw).get("status"),
            "freshness": freshness(value["published_at"], value["entered_at"], at)}


class KnowledgeQueryRepository(InfoOSRepository):
    @staticmethod
    def context(conn: sqlite3.Connection, card_id: str, at: str) -> dict[str, Any] | None:
        row = conn.execute(CONTEXT + " where c.card_id=?", (EXTRACTION_VERSION, card_id)).fetchone()
        return candidate(row, at) if row is not None else None

    def candidates(self, since: str, until: str | None, after: tuple[str, str] | None, limit: int) -> list[dict[str, Any]]:
        clauses, params = ["c.status='active'", f"{ENTRY}>=?"], [EXTRACTION_VERSION, since]
        if until is not None:
            clauses.append(f"{ENTRY}<?")
            params.append(until)
        rows = []
        at = utc_now_micro_z()
        with self.connect_existing() as conn:
            # Scan in bounded pages; deterministic channel semantics live in domain.
            while len(rows) <= limit:
                seek = f" and ({ENTRY},c.card_id)>(?,?)" if after is not None else ""
                batch = conn.execute(CONTEXT + " where " + " and ".join(clauses) + seek
                    + f" order by {ENTRY},c.card_id limit 200", [*params, *(after if after else ())]).fetchall()
                if not batch:
                    break
                for row in batch:
                    selected = candidate(row, at)
                    if selected is not None:
                        rows.append(selected)
                        if len(rows) > limit:
                            return rows
                after = (batch[-1]["entered_at"], batch[-1]["card_id"])
        return rows

    @staticmethod
    def get(conn: sqlite3.Connection, knowledge_id: str) -> dict[str, Any] | None:
        row = conn.execute(f"select {COLUMNS} from knowledge_cards where knowledge_id=?", (knowledge_id,)).fetchone()
        return decode(row) if row is not None else None

    def search(self, kind: str, query: str, limit: int) -> list[dict[str, Any]]:
        terms, words = search_terms(query, ("title", "fields_json"))
        clauses, params = ["kind=?", "status!='superseded'", *terms], [kind, *words]
        with self.connect_existing() as conn:
            rows = conn.execute(f"select {COLUMNS} from knowledge_cards where " + " and ".join(clauses)
                                + " order by updated_at desc,knowledge_id desc limit ?", [*params, limit]).fetchall()
        return [decode(row) for row in rows]

    def browse(self, *, kind: str | None, subtype: str | None, research_status: str | None,
               query: str, after: tuple[str, str] | None, limit: int) -> list[dict[str, Any]]:
        terms, params = search_terms(query, ("k.title", "k.use_note", "k.fields_json"))
        clauses = ["k.status!='superseded'", "k.kind in (" + ",".join("?" for k in KINDS.values() if k.enabled) + ")", *terms]
        params = [k.key for k in KINDS.values() if k.enabled] + params
        for key, value in (("kind", kind), ("subtype", subtype), ("research_status", research_status)):
            if value is not None:
                clauses.append(f"k.{key}=?")
                params.append(value)
        if after is not None:
            clauses.append("(k.updated_at,k.knowledge_id)<(?,?)")
            params.extend(after)
        with self.connect_existing() as conn:
            rows = conn.execute("""select k.knowledge_id,k.kind,k.subtype,k.title,k.use_note,
                k.attribution_kind,k.attribution_ref,k.strength,k.research_status,k.origin_freshness,
                k.created_at,k.updated_at,coalesce(e.evidence_count,0) as evidence_count,
                coalesce(e.has_conflict,0) as has_conflict from knowledge_cards k left join
                (select knowledge_id,count(*) as evidence_count,max(action='conflict') as has_conflict
                    from knowledge_evidence group by knowledge_id) e on e.knowledge_id=k.knowledge_id
                where """ + " and ".join(clauses) + " order by k.updated_at desc,k.knowledge_id desc limit ?",
                [*params, limit + 1]).fetchall()
        return [dict(row) for row in rows]

    def facets(self) -> list[dict[str, Any]]:
        with self.connect_existing() as conn:
            return [dict(row) for row in conn.execute("""select kind,subtype,count(*) as count
                from knowledge_cards where status!='superseded' group by kind,subtype""")]

    def page(self, *, kind: str | None, since: str | None, research_status: str | None,
             attribution_ref: str | None, after: tuple[str, str] | None, limit: int) -> list[dict[str, Any]]:
        clauses, params = [], []
        for key, value in (("kind", kind), ("research_status", research_status), ("attribution_ref", attribution_ref)):
            if value is not None:
                clauses.append(f"{key}=?")
                params.append(value)
        if since is not None:
            clauses.append("updated_at>=?")
            params.append(since)
        if after is not None:
            clauses.append("(updated_at,knowledge_id)<(?,?)")
            params.extend(after)
        with self.connect_existing() as conn:
            rows = conn.execute(f"select {COLUMNS} from knowledge_cards"
                + (" where " + " and ".join(clauses) if clauses else "")
                + " order by updated_at desc,knowledge_id desc limit ?", [*params, limit + 1]).fetchall()
        return [decode(row) for row in rows]

    @staticmethod
    def for_card(conn: sqlite3.Connection, card_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in conn.execute("""select k.knowledge_id,k.kind,k.title from knowledge_cards k
            join knowledge_evidence e on e.knowledge_id=k.knowledge_id where e.card_id=?
            order by k.created_at,k.knowledge_id""", (card_id,))]

    def detail(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.connect_existing() as conn:
            value = self.get(conn, knowledge_id)
            if value is None:
                return None
            evidence = conn.execute("""select e.evidence_id,e.card_id,e.extraction_id,e.action,e.quotes_json,e.note,
                e.created_at,c.title,c.source_platform as platform,c.published_at from knowledge_evidence e
                join cards c on c.card_id=e.card_id where e.knowledge_id=? order by e.created_at,e.evidence_id""",
                (knowledge_id,)).fetchall()
            value["evidence"] = [{**dict(row), "quotes": json.loads(row["quotes_json"])} for row in evidence]
            for row in value["evidence"]:
                del row["quotes_json"]
            value["status_history"] = [dict(row) for row in conn.execute("""select event_id,field,value,note,actor,created_at
                from knowledge_status_events where knowledge_id=? order by created_at,event_id""", (knowledge_id,))]
            value["related_cards"] = [dict(row) for row in conn.execute("""select distinct k.knowledge_id,k.kind,k.title
                from knowledge_cards k join knowledge_evidence sibling on sibling.knowledge_id=k.knowledge_id
                where k.knowledge_id!=? and sibling.card_id in
                    (select card_id from knowledge_evidence where knowledge_id=?)
                order by k.knowledge_id""", (knowledge_id, knowledge_id))]
        return value
