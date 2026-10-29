"""Append-only verification persistence and indexed, deterministic reads."""
import json
import sqlite3
from typing import Any, NotRequired, TypedDict, cast

from backend.domain.intelligence.verification import FIELDS
from backend.infrastructure.storage.base import InfoOSRepository


class OpportunityVerificationRow(TypedDict):
    verification_id: str
    card_id: str
    recorder: str
    contact_channel: str
    offer: str
    credibility: str
    conclusion: str
    contacted_on: str | None
    price: str | None
    delivery: str | None
    funnel: str | None
    doubts: str | None
    replicability: int | None
    note: str | None
    supersedes: str | None
    token_id: str
    schema_version: str
    created_at: str
    client_verification_id: str | None
    request_content_hash: str | None
    breakdown_id: str | None
    expected_breakdown_hash: str | None
    breakdown_snapshot: object


class OpportunityVerificationListRow(OpportunityVerificationRow):
    title: str
    platform: str
    superseded_by: str | None
    is_digest: NotRequired[bool | None]


COLUMNS = ("verification_id", *FIELDS, "token_id", "schema_version", "created_at",
           "client_verification_id", "request_content_hash", "breakdown_id", "expected_breakdown_hash", "breakdown_snapshot")


def decode(row: sqlite3.Row) -> OpportunityVerificationRow:
    result = cast(OpportunityVerificationRow, dict(row))
    result["breakdown_snapshot"] = json.loads(result["breakdown_snapshot"]) if result["breakdown_snapshot"] else None
    return result


class OpportunityVerificationRepository(InfoOSRepository):
    @staticmethod
    def append(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
        """Caller owns the transaction; there is deliberately no update/delete use case."""
        conn.execute(f"insert into opportunity_verifications ({', '.join(COLUMNS)}) "
                     f"values ({', '.join(':' + field for field in COLUMNS)})",
                     {**payload, "breakdown_snapshot": json.dumps(payload["breakdown_snapshot"], ensure_ascii=False)
                      if payload["breakdown_snapshot"] is not None else None})

    @staticmethod
    def find_submission(conn: sqlite3.Connection, token_id: str, client_verification_id: str) -> OpportunityVerificationRow | None:
        row = conn.execute(f"select {', '.join(COLUMNS)} from opportunity_verifications "
                           "where token_id=? and client_verification_id=?",
                           (token_id, client_verification_id)).fetchone()
        return decode(row) if row else None

    @staticmethod
    def latest_conclusion(conn: sqlite3.Connection, card_id: str) -> sqlite3.Row | None:
        """Newest record that no correction supersedes: its conclusion and time."""
        return conn.execute("select conclusion, created_at from opportunity_verifications v where card_id=? "
                            "and not exists(select 1 from opportunity_verifications n where n.supersedes=v.verification_id) "
                            "order by created_at desc, verification_id desc limit 1", (card_id,)).fetchone()

    @staticmethod
    def belongs_to_card(conn: sqlite3.Connection, verification_id: str, card_id: str) -> bool:
        return conn.execute("select 1 from opportunity_verifications where verification_id=? and card_id=?",
                            (verification_id, card_id)).fetchone() is not None

    def page(self, *, card_id: str | None = None, since: str | None = None, conclusion: str | None = None,
             after: tuple[str, str] | None = None, limit: int = 20) -> list[OpportunityVerificationListRow]:
        clauses, params = [], []
        for value, clause in ((card_id, "v.card_id=?"), (since, "v.created_at>=?"), (conclusion, "v.conclusion=?")):
            if value is not None:
                clauses.append(clause)
                params.append(value)
        if after:
            clauses.extend(["v.created_at<=?", "(v.created_at,v.verification_id)<(?,?)"])
            params.extend([after[0], *after])
        where = " and ".join(clauses) or "1=1"
        with self.connect_existing() as conn:
            rows = conn.execute("select " + ", ".join(f"v.{f}" for f in COLUMNS) +
                ", c.title, c.source_platform as platform, "
                "(select n.verification_id from opportunity_verifications n "
                "where n.card_id=v.card_id and n.supersedes=v.verification_id "
                "order by n.created_at desc,n.verification_id desc limit 1) as superseded_by "
                "from opportunity_verifications v join cards c on c.card_id=v.card_id where " + where +
                " order by v.created_at desc,v.verification_id desc limit ?", (*params, limit + 1)).fetchall()
        return [cast(OpportunityVerificationListRow, decode(row)) for row in rows]
