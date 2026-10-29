"""Shared read-only facts for board, detail and statistics projections."""
import json
from pathlib import Path
from typing import cast
from urllib.parse import quote

from backend.domain.intelligence.opportunity import FIELD_CONSTRAINTS
from backend.domain.intelligence.opportunity_follow_up import derive_stage
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.card_entry import project_display_titles
from backend.infrastructure.storage.opportunity_follow_ups import OpportunityFollowUpRepository
from backend.infrastructure.storage.opportunity_breakdowns import FollowUpSummary, OpportunityListRow
from backend.infrastructure.storage.opportunity_candidates import CANDIDATE_KIND
from backend.read_models.opportunity_board_types import (
    BreakdownRow, RawBreakdownRow, VerificationRow,
)

BREAKDOWN_FIELDS = ("breakdown_id", "card_id", "created_at", "writeback_at", "evaluator", "lens_version",
                    *FIELD_CONSTRAINTS)
LATEST_BREAKDOWN = """b.breakdown_id=(select n.breakdown_id from opportunity_breakdowns n
    where n.card_id=b.card_id order by n.created_at desc,n.breakdown_id desc limit 1)"""


def breakdowns(base_dir: Path, card_id: str | None = None) -> list[BreakdownRow]:
    params = []
    where = LATEST_BREAKDOWN
    if card_id is not None:
        where += " and b.card_id=?"
        params.append(card_id)
    with InfoOSRepository(base_dir).connect_existing() as conn:
        rows = conn.execute("select " + ",".join(f"b.{field}" for field in BREAKDOWN_FIELDS) +
            ",c.title,c.source_author as author,c.source_platform as platform,display_source.display_name as source_name,"
            f"c.source_url,c.published_at,c.raw_payload,{CANDIDATE_KIND} as candidate_kind "
            "from opportunity_breakdowns b join cards c on c.card_id=b.card_id "
            "left join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id "
            "left join sources s on s.source_id=r.source_id "
            "left join sources display_source on display_source.source_id=c.source_id where " + where, params).fetchall()
    raw_rows = []
    for row in rows:
        values = dict(row)
        values["author_url"] = author_profile_url(values["platform"], json.loads(values.pop("raw_payload")))
        raw_rows.append(cast(RawBreakdownRow, values))
    result: list[BreakdownRow] = [{**row, "risk_flags": json.loads(row["risk_flags"]),
        "evidence": json.loads(row["evidence"]), "verify_questions": json.loads(row["verify_questions"])}
        for row in raw_rows]
    project_display_titles(base_dir, result)
    return result


def author_profile_url(platform: str, payload: object) -> str | None:
    """Extract account metadata from the historical card JSON boundary."""
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("raw_payload", payload)
    if not isinstance(metadata, dict):
        return None
    record = metadata.get("opencli_record", {})
    if not isinstance(record, dict):
        return None
    url = record.get("author_url")
    if isinstance(url, str) and url:
        return url
    field = {"douyin": "sec_uid", "xiaohongshu": "author_user_id"}.get(platform)
    user_id = record.get(field) if field is not None else None
    if not isinstance(user_id, str) or not user_id:
        return None
    origin = "https://www.douyin.com/user/" if platform == "douyin" else "https://www.xiaohongshu.com/user/profile/"
    return origin + quote(user_id, safe="")


def verifications(base_dir: Path, card_id: str | None = None, *, card_ids: list[str] | None = None) -> list[VerificationRow]:
    fields = ("verification_id", "card_id", "created_at", "conclusion", "credibility", "contacted_on",
              "contact_channel", "offer", "price", "delivery", "doubts", "note")
    where, params = ("where v.card_id=?", [card_id]) if card_id is not None else ("", [])
    if card_ids is not None:
        if not card_ids:
            return []
        where, params = f"where v.card_id in ({','.join('?' for _ in card_ids)})", card_ids
    with InfoOSRepository(base_dir).connect_existing() as conn:
        rows = conn.execute("select " + ",".join(f"v.{field}" for field in fields) + ",exists(select 1 from opportunity_verifications n "
            "where n.supersedes=v.verification_id) as superseded "
            "from opportunity_verifications v " + where + " order by v.created_at,v.verification_id", params).fetchall()
    return [cast(VerificationRow, dict(row)) for row in rows]


def latest_verifications(rows: list[VerificationRow]) -> dict[str, VerificationRow]:
    return {row["card_id"]: row for row in rows if not row["superseded"]}


def follow_up_summary(base_dir: Path, rows: list[OpportunityListRow]) -> dict[str, FollowUpSummary]:
    card_ids = [row["card_id"] for row in rows]
    latest = OpportunityFollowUpRepository(base_dir).latest(card_ids)
    verified = latest_verifications(verifications(base_dir, card_ids=card_ids))
    result: dict[str, FollowUpSummary] = {}
    for row in rows:
        event = latest.get(row["card_id"])
        stage = derive_stage(row["next_step"], row["created_at"], event, verified.get(row["card_id"]))
        verification = verified.get(row["card_id"])
        updates = [fact["created_at"] for fact in (event, verification) if fact is not None]
        result[row["card_id"]] = {"stage": stage["stage"], "state": event["state"] if event else None,
            "updated_at": max(updates, key=parse_utc) if updates else row["created_at"]}
    return result
