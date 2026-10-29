"""Persist the user's research decision without asking scheduled agents to decide."""
from pathlib import Path

from backend.domain.intelligence.knowledge import RESEARCH_STATUSES
from backend.domain.timestamps import parse_utc, utc_now_micro_z
from backend.infrastructure.storage.knowledge_cards import KnowledgeCardRepository
from backend.infrastructure.storage.knowledge_queries import KnowledgeQueryRepository


def set_research_status(base_dir: Path, knowledge_id: str, status: str, note: str | None, *, actor: str) -> dict:
    if status not in RESEARCH_STATUSES:
        raise ValueError("invalid_research_status")
    if note is not None and (not isinstance(note, str) or len(note.strip()) > 200):
        raise ValueError("invalid_note")
    with KnowledgeQueryRepository(base_dir).connect() as conn:
        conn.execute("begin immediate")
        card = KnowledgeQueryRepository.get(conn, knowledge_id)
        if card is None or card["kind"] != "opportunity":
            raise ValueError("not_opportunity_card")
        if card["research_status"] == status:
            return {"knowledge_id": knowledge_id, "research_status": status, "changed": False}
        event_id = KnowledgeCardRepository.research_status(conn, knowledge_id, status,
            note.strip() if note is not None else None, actor, parse_utc(utc_now_micro_z()).isoformat(timespec="microseconds").replace("+00:00", "Z"))
    return {"knowledge_id": knowledge_id, "research_status": status, "changed": True, "event_id": event_id}
