"""Transactional breakdown persistence and indexed latest-per-card reads."""
import json
import sqlite3
from typing import Any, TypedDict, cast

from backend.domain.intelligence.opportunity import FIELD_CONSTRAINTS, breakdown_snapshot
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository


class OpportunityBreakdownRow(TypedDict):
    breakdown_id: str
    card_id: str
    card_version: int
    lens_version: str
    rules_version: str
    evaluator: str
    token_id: str
    profile_version: int | None
    raw_item_revision: int | None
    monetization_model: str
    offer: str
    price_signal: str | None
    funnel: str
    target_customer: str | None
    evidence: object
    replicability: int
    credibility: int
    risk_flags: object
    next_step: str
    verify_questions: object
    breakdown_note: str
    created_at: str
    writeback_at: str | None


class OpportunityBreakdownSnapshot(OpportunityBreakdownRow):
    breakdown_content_hash: str
    profile_is_current: bool


LIST_FIELDS = ("evidence", "risk_flags", "verify_questions")
COLUMNS = ("breakdown_id", "card_id", "card_version", "lens_version", "rules_version", "evaluator",
           "token_id", "profile_version", "raw_item_revision", *FIELD_CONSTRAINTS, "created_at", "writeback_at")


def decode(row: sqlite3.Row | None) -> OpportunityBreakdownRow | None:
    if row is None:
        return None
    result = cast(OpportunityBreakdownRow, dict(row))
    for field in LIST_FIELDS:
        if field in result:
            result[field] = json.loads(result[field])
    return result


class OpportunityBreakdownRepository(InfoOSRepository):
    @staticmethod
    def upsert(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
        """Caller owns the transaction; preserve the unique key's first created_at."""
        values = {**payload, "created_at": parse_utc(payload["created_at"]).isoformat(
            timespec="microseconds").replace("+00:00", "Z")}
        values["writeback_at"] = values["created_at"]
        for field in LIST_FIELDS:
            values[field] = json.dumps(values[field], ensure_ascii=False)
        immutable = {"breakdown_id", "card_id", "card_version", "lens_version", "created_at"}
        update = ", ".join(f"{field}=excluded.{field}" for field in COLUMNS if field not in immutable)
        conn.execute(f"insert into opportunity_breakdowns ({', '.join(COLUMNS)}) values "
                     f"({', '.join(':' + field for field in COLUMNS)}) "
                     f"on conflict(card_id, card_version, lens_version) do update set {update}", values)

    def latest_for_card(self, card_id: str, *, connection: sqlite3.Connection | None = None) -> OpportunityBreakdownSnapshot | None:
        if connection is None:
            with self.connect_existing() as conn:
                return self.latest_for_card(card_id, connection=conn)
        row = connection.execute(f"select {', '.join(COLUMNS)} from opportunity_breakdowns "
                                 "where card_id=? order by created_at desc, breakdown_id desc limit 1",
                                 (card_id,)).fetchone()
        if row is None:
            return None
        profile = InterestProfileRepository(self.base_dir).get(connection=connection)
        return {**cast(OpportunityBreakdownSnapshot, breakdown_snapshot(decode(row))), "profile_is_current": bool(
            profile and row["profile_version"] == profile["profile_version"])}
