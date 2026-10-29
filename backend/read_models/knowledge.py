"""Knowledge library projections with opaque, filter-bound cursors."""
import base64
import json
from pathlib import Path

from backend.domain.json_types import JsonObject
from backend.domain.intelligence.knowledge import EXTRACTION_VERSION, RESEARCH_STATUSES
from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.infrastructure.runtime.environment import public_base_url
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.knowledge_queries import KnowledgeQueryRepository
from backend.read_models.opportunities import _context, _cursor, _limit, _stamp


def _link(knowledge_id: str) -> str:
    origin = public_base_url()
    return (origin if origin is not None else "") + f"/r/knowledge/{knowledge_id}"


def _page(rows: list[JsonObject], limit: int, context: str, time_field: str, id_field: str) -> JsonObject:
    items, cursor = rows[:limit], None
    if len(rows) > limit:
        last = items[-1]
        cursor = base64.urlsafe_b64encode(json.dumps({"context": context, "time": last[time_field],
                                                    "id": last[id_field]}).encode()).decode()
    for item in items:
        item["deep_link"] = _link(item["knowledge_id"]) if id_field == "knowledge_id" else (
            (public_base_url() or "") + f"/r/card/{item['card_id']}")
    return {"items": items, "next_cursor": cursor}


def list_extraction_candidates(base_dir: Path, since: str, until: str | None = None,
                               limit: int = 50, cursor: str | None = None) -> JsonObject:
    _limit(limit)
    start, end = _stamp(since, entry=True), _stamp(until, entry=True) if until is not None else None
    if end is not None and start > end:
        raise ValueError("invalid_window")
    profile = InterestProfileRepository(base_dir).get()
    version = profile["profile_version"] if profile is not None else None
    context = _context(["extractions", start, end, EXTRACTION_VERSION, version])
    rows = KnowledgeQueryRepository(base_dir).candidates(start, end, _cursor(cursor, context), limit)
    return {**_page(rows, limit, context, "entered_at", "card_id"), "profile_version": version}


def search_knowledge_cards(base_dir: Path, kind: str, query: str, limit: int = 10) -> JsonObject:
    if kind not in KINDS:
        raise ValueError("invalid_kind")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("invalid_limit")
    if not query.strip():
        raise ValueError("query_required")
    rows = KnowledgeQueryRepository(base_dir).search(kind, query, limit)
    return {"items": [{**row, "deep_link": _link(row["knowledge_id"])} for row in rows]}


def list_knowledge_cards(base_dir: Path, kind: str | None = None, since: str | None = None,
                         research_status: str | None = None, attribution_ref: str | None = None,
                         limit: int = 20, cursor: str | None = None) -> JsonObject:
    _limit(limit)
    if kind is not None and kind not in KINDS:
        raise ValueError("invalid_kind")
    if research_status is not None and research_status not in RESEARCH_STATUSES:
        raise ValueError("invalid_research_status")
    start = _stamp(since) if since is not None else None
    context = _context(["knowledge", kind, start, research_status, attribution_ref])
    rows = KnowledgeQueryRepository(base_dir).page(kind=kind, since=start, research_status=research_status,
        attribution_ref=attribution_ref, after=_cursor(cursor, context), limit=limit)
    return _page(rows, limit, context, "updated_at", "knowledge_id")


def browse_knowledge_library(base_dir: Path, kind: str | None = None, subtype: str | None = None,
                             research_status: str | None = None, query: str | None = None,
                             limit: int = 20, cursor: str | None = None) -> JsonObject:
    _limit(limit)
    if kind is not None and (kind not in KINDS or not KINDS[kind].enabled):
        raise ValueError("invalid_kind")
    if subtype is not None and (kind is None or subtype not in KINDS[kind].subtypes):
        raise ValueError("invalid_subtype")
    if research_status is not None and research_status not in RESEARCH_STATUSES:
        raise ValueError("invalid_research_status")
    query = " ".join((query or "").split())
    context = _context(["knowledge_library", kind, subtype, research_status, query])
    repository = KnowledgeQueryRepository(base_dir)
    rows = repository.browse(kind=kind, subtype=subtype, research_status=research_status, query=query,
                             after=_cursor(cursor, context), limit=limit)
    page = _page(rows, limit, context, "updated_at", "knowledge_id")
    for item in page["items"]:
        schema = KINDS[item["kind"]]
        item.update(kind_label=schema.label, subtype_label=schema.subtypes[item["subtype"]],
                    has_conflict=bool(item["has_conflict"]))
    counts = {(row["kind"], row["subtype"]): row["count"] for row in repository.facets()}
    page["facets"] = [{"kind": key, "label": schema.label,
        "count": sum(counts.get((key, subtype), 0) for subtype in schema.subtypes),
        "subtypes": [{"subtype": subtype, "label": label, "count": counts.get((key, subtype), 0)}
                     for subtype, label in schema.subtypes.items()]} for key, schema in KINDS.items() if schema.enabled]
    return page


def knowledge_for_card(base_dir: Path | str, card_id: str) -> list[JsonObject]:
    with KnowledgeQueryRepository(base_dir).connect_existing() as conn:
        rows = KnowledgeQueryRepository.for_card(conn, card_id)
    return [{**row, "deep_link": _link(row["knowledge_id"])} for row in rows]


def get_knowledge_card(base_dir: Path, knowledge_id: str) -> JsonObject | None:
    value = KnowledgeQueryRepository(base_dir).detail(knowledge_id)
    if value is None:
        return None
    kind = KINDS[value["kind"]]
    value.update(kind_label=kind.label, subtype_label=kind.subtypes[value["subtype"]],
                 field_schema=kind.fields, deep_link=_link(knowledge_id),
                 has_conflict=any(row["action"] == "conflict" for row in value["evidence"]))
    origin = public_base_url()
    for row in value["evidence"]:
        row["deep_link"] = (origin if origin is not None else "") + f"/r/card/{row['card_id']}"
    for row in value["related_cards"]:
        row["deep_link"] = _link(row["knowledge_id"])
    return value
