"""Knowledge writes use only the caller's transaction."""
import sqlite3
from typing import Any
from uuid import uuid4

from backend.domain.intelligence.knowledge import EXTRACTION_VERSION
from backend.infrastructure.storage.base import dumps


class KnowledgeCardRepository:
    @staticmethod
    def extraction(conn: sqlite3.Connection, *, card_id: str, card_version: int, profile_version: int,
                   channel: str, outcome: str, nothing_reason: str | None, evaluator: str, token_id: str,
                   now: str) -> str:
        extraction_id = "kx_" + uuid4().hex
        conn.execute("""insert into knowledge_extractions
            (extraction_id,card_id,card_version,extraction_version,profile_version,channel,outcome,
             nothing_reason,evaluator,token_id,created_at) values (?,?,?,?,?,?,?,?,?,?,?)""",
            (extraction_id, card_id, card_version, EXTRACTION_VERSION, profile_version, channel,
             outcome, nothing_reason, evaluator, token_id, now))
        return extraction_id

    @staticmethod
    def write(conn: sqlite3.Connection, value: dict[str, Any], *, knowledge_id: str, card_id: str,
              extraction_id: str, profile_version: int, evaluator: str, published_at: str | None,
              origin_freshness: str, now: str) -> None:
        action = value["action"]
        if action == "new":
            conn.execute("""insert into knowledge_cards
                (knowledge_id,kind,subtype,title,fields_json,attribution_kind,attribution_ref,use_note,strength,
                 origin_published_at,origin_freshness,research_status,extraction_version,profile_version,evaluator,
                 created_at,updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (knowledge_id, value["kind"], value["subtype"], value["title"], dumps(value["fields"]),
                 value["attribution_kind"], value["attribution_ref"], value["use_note"], value["strength"],
                 published_at, origin_freshness, "pending" if value["kind"] == "opportunity" else None,
                 EXTRACTION_VERSION, profile_version, evaluator, now, now))
        elif action == "update":
            conn.execute("""update knowledge_cards set fields_json=?,strength=?,use_note=?,updated_at=?
                where knowledge_id=?""", (dumps(value["fields"]), value["strength"], value["use_note"], now, knowledge_id))
        else:
            conn.execute("update knowledge_cards set updated_at=? where knowledge_id=?", (now, knowledge_id))
        if action != "new":
            conn.execute("""update knowledge_cards set origin_published_at=case
                when ? is not null and (origin_published_at is null or origin_published_at>?) then ?
                else origin_published_at end where knowledge_id=?""", (published_at, published_at, published_at, knowledge_id))
        conn.execute("""insert into knowledge_evidence
            (evidence_id,knowledge_id,card_id,extraction_id,action,quotes_json,note,created_at) values (?,?,?,?,?,?,?,?)""",
            ("ke_" + uuid4().hex, knowledge_id, card_id, extraction_id, action, dumps(value["quotes"]), value["note"], now))

    @staticmethod
    def research_status(conn: sqlite3.Connection, knowledge_id: str, status: str, note: str | None,
                        actor: str, now: str) -> str:
        event_id = "ks_" + uuid4().hex
        conn.execute("update knowledge_cards set research_status=?,updated_at=? where knowledge_id=?",
                     (status, now, knowledge_id))
        conn.execute("""insert into knowledge_status_events (event_id,knowledge_id,field,value,note,actor,created_at)
            values (?,?,'research_status',?,?,?,?)""", (event_id, knowledge_id, status, note, actor, now))
        return event_id
