"""Transactional breakdown persistence and indexed latest-per-card reads."""
import json
import sqlite3
from typing import Any, NotRequired, TypedDict, cast

from backend.domain.intelligence.opportunity import CandidateKind, FIELD_CONSTRAINTS, breakdown_snapshot
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.opportunity_candidates import CANDIDATE_KIND


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


class FollowUpSummary(TypedDict):
    stage: str | None
    state: str | None
    updated_at: str


class OpportunityListRow(TypedDict):
    candidate_kind: CandidateKind | None
    follow_up: NotRequired[FollowUpSummary]
    breakdown_id: str
    card_id: str
    created_at: str
    next_step: str
    monetization_model: str
    offer: str
    replicability: int
    credibility: int
    risk_flags: object
    profile_version: int | None
    title: str
    platform: str
    verification_count: int
    latest_verification_conclusion: str | None
    profile_is_current: bool
    is_digest: NotRequired[bool | None]


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

    @staticmethod
    def get(conn: sqlite3.Connection, breakdown_id: str) -> OpportunityBreakdownRow | None:
        row = conn.execute(f"select {', '.join(COLUMNS)} from opportunity_breakdowns where breakdown_id=?",
                           (breakdown_id,)).fetchone()
        return decode(row)

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

    def list_latest(self, *, since: str | None, next_step: str | None, min_replicability: int | None,
                    after: tuple[str, str] | None, limit: int) -> list[OpportunityListRow]:
        # Select the latest row before filtering so old matching versions cannot reappear.
        clauses = ["b.breakdown_id = (select n.breakdown_id from opportunity_breakdowns n "
                   "where n.card_id=b.card_id order by n.created_at desc, n.breakdown_id desc limit 1)"]
        params = []
        for value, clause in ((since, "b.created_at >= ?"), (next_step, "b.next_step = ?"),
                              (min_replicability, "b.replicability >= ?")):
            if value is not None:
                clauses.append(clause)
                params.append(value)
        if after:
            clauses.extend(["b.created_at <= ?", "(b.created_at, b.breakdown_id) < (?, ?)"])
            params.extend([after[0], *after])
        fields = ("breakdown_id", "card_id", "created_at", "next_step", "monetization_model", "offer",
                  "replicability", "credibility", "risk_flags", "profile_version")
        with self.connect_existing() as conn:
            rows = conn.execute("select " + ", ".join(f"b.{f}" for f in fields) +
                f", c.title, c.source_platform as platform, {CANDIDATE_KIND} as candidate_kind, "
                "(select count(*) from opportunity_verifications v where v.card_id=b.card_id) as verification_count, "
                "(select v.conclusion from opportunity_verifications v where v.card_id=b.card_id "
                "order by v.created_at desc,v.verification_id desc limit 1) as latest_verification_conclusion "
                "from opportunity_breakdowns b join cards c "
                "on c.card_id=b.card_id "
                "left join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id "
                "left join raw_item_candidate_flags f on f.connector_id=r.connector_id and f.raw_item_id=r.raw_item_id "
                "left join sources s on s.source_id=r.source_id where " + " and ".join(clauses) +
                " order by b.created_at desc, b.breakdown_id desc limit ?", (*params, limit + 1)).fetchall()
            profile = InterestProfileRepository(self.base_dir).get(connection=conn)
        return [{**cast(OpportunityListRow, decode(row)), "profile_is_current": bool(
            profile and row["profile_version"] == profile["profile_version"])} for row in rows]
