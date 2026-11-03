"""Five research-status columns over opportunity knowledge cards."""
from pathlib import Path
from typing import TypedDict

from backend.domain.intelligence.knowledge import RESEARCH_LABELS, RESEARCH_STATUSES
from backend.domain.intelligence.knowledge_kinds import KINDS
from backend.infrastructure.storage.opportunity_knowledge_queries import OpportunityBoardRow, OpportunityKnowledgeQueryRepository


class BoardItem(OpportunityBoardRow):
    pass


class BoardColumn(TypedDict):
    status: str
    label: str
    total: int
    items: list[BoardItem]
    next_offset: int | None


class BoardData(TypedDict):
    columns: list[BoardColumn]


def board(base_dir: Path | str, *, platform: str | None = None, subtype: str | None = None,
          query: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> BoardData:
    if status is not None and status not in RESEARCH_STATUSES:
        raise ValueError("invalid_research_status")
    if subtype is not None and subtype not in KINDS["opportunity"].subtypes:
        raise ValueError("invalid_subtype")
    rows = OpportunityKnowledgeQueryRepository(base_dir).board(status=status, subtype=subtype, platform=platform,
        query=" ".join((query or "").split()), limit=limit, offset=offset)
    columns: list[BoardColumn] = []
    for value in (status,) if status is not None else RESEARCH_STATUSES:
        total = rows["totals"].get(value, 0)
        columns.append({"status": value, "label": RESEARCH_LABELS[value], "total": total,
            "items": [BoardItem(**row) for row in rows["items"] if row["research_status"] == value],
            "next_offset": offset + limit if offset + limit < total else None})
    return {"columns": columns}
