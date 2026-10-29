"""Shared read-only facts for board, detail and statistics projections."""
import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote

from backend.domain.card_titles import display_title, title_needs_body
from backend.domain.intelligence.opportunity import FIELD_CONSTRAINTS
from backend.domain.intelligence.opportunity_follow_up import FollowUpFact, Stage, VerificationFact, derive_stage
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.card_entry import card_display_fields
from backend.infrastructure.storage.opportunity_follow_ups import OpportunityFollowUpRepository
from backend.infrastructure.storage.opportunity_breakdowns import FollowUpSummary, OpportunityListRow
from backend.infrastructure.storage.opportunity_candidates import CANDIDATE_KIND
from backend.read_models.opportunity_board_types import (
    BoardRow, BreakdownRow, RawBreakdownRow,
    StageBreakdownRow, StatsBreakdownRow, VerificationRow,
)

BREAKDOWN_FIELDS = ("breakdown_id", "card_id", "created_at", "writeback_at", "evaluator", "lens_version",
                    *FIELD_CONSTRAINTS)
LATEST_BREAKDOWN = """b.breakdown_id=(select n.breakdown_id from opportunity_breakdowns n
    where n.card_id=b.card_id order by n.created_at desc,n.breakdown_id desc limit 1)"""


def breakdowns(base_dir: Path, card_id: str) -> list[BreakdownRow]:
    """Load complete detail arrays, sharing card display decoding."""
    params = [card_id]
    where = LATEST_BREAKDOWN + " and b.card_id=?"
    fields = BREAKDOWN_FIELDS
    with InfoOSRepository(base_dir).connect_existing() as conn:
        rows = conn.execute("select " + ",".join(f"b.{field}" for field in fields) +
            ",c.title,c.source_author as author,c.source_platform as platform,display_source.display_name as source_name,"
            f"c.source_url,c.published_at,c.content,c.raw_payload,{CANDIDATE_KIND} as candidate_kind "
            "from opportunity_breakdowns b join cards c on c.card_id=b.card_id "
            "left join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id "
            "left join raw_item_candidate_flags f on f.connector_id=r.connector_id and f.raw_item_id=r.raw_item_id "
            "left join sources s on s.source_id=r.source_id "
            "left join sources display_source on display_source.source_id=c.source_id where " + where, params).fetchall()
    detail_rows: list[BreakdownRow] = []
    for row in rows:
        values = dict(row)
        payload = json.loads(values.pop("raw_payload"))
        values["author_url"] = author_profile_url(values["platform"], payload)
        values.update(card_display_fields(values["platform"], values["title"], values.pop("content"), payload))
        detail_row = cast(RawBreakdownRow, values)
        detail_rows.append({**detail_row, "risk_flags": json.loads(detail_row["risk_flags"]),
            "evidence": json.loads(detail_row["evidence"]),
            "verify_questions": json.loads(detail_row["verify_questions"])})
    return detail_rows


def board_rows(base_dir: Path) -> list[BoardRow]:
    """Load latest board facts without card bodies or payloads."""
    fields = tuple(field for field in BREAKDOWN_FIELDS if field not in ("evidence", "verify_questions"))
    with InfoOSRepository(base_dir).connect_existing() as conn:
        rows = conn.execute("select " + ",".join(f"b.{field}" for field in fields) +
            ",json_array_length(b.verify_questions) as question_count,"
            "c.title,c.source_author as author,c.source_platform as platform,display_source.display_name as source_name,"
            f"c.source_url,c.published_at,{CANDIDATE_KIND} as candidate_kind "
            "from opportunity_breakdowns b join cards c on c.card_id=b.card_id "
            "left join raw_items r on r.connector_id=c.raw_connector_id and r.raw_item_id=c.raw_item_id "
            "left join raw_item_candidate_flags f on f.connector_id=r.connector_id and f.raw_item_id=r.raw_item_id "
            "left join sources s on s.source_id=r.source_id "
            "left join sources display_source on display_source.source_id=c.source_id where " + LATEST_BREAKDOWN).fetchall()
    return [cast(BoardRow, {**dict(row), "risk_flags": json.loads(row["risk_flags"])}) for row in rows]


_ID_BATCH = 500


def _rows_by_card_id(conn: sqlite3.Connection, columns: str, card_ids: Sequence[str]) -> Iterator[sqlite3.Row]:
    for start in range(0, len(card_ids), _ID_BATCH):
        batch = card_ids[start:start + _ID_BATCH]
        yield from conn.execute(f"select card_id,{columns} from cards where card_id in "
                                f"({','.join('?' for _ in batch)})", batch)


def display_titles(base_dir: Path, items: Sequence[tuple[str, str]]) -> dict[str, str]:
    ids = [card_id for card_id, platform in items if title_needs_body(platform)]
    if not ids:
        return {}
    with InfoOSRepository(base_dir).connect_existing() as conn:
        return {row["card_id"]: display_title(row["source_platform"], row["title"], row["content"])
                for row in _rows_by_card_id(conn, "source_platform,title,content", ids)}


def author_urls(base_dir: Path, card_ids: Sequence[str]) -> dict[str, str | None]:
    if not card_ids:
        return {}
    with InfoOSRepository(base_dir).connect_existing() as conn:
        return {row["card_id"]: author_profile_url(row["source_platform"], json.loads(row["raw_payload"]))
                for row in _rows_by_card_id(conn, "source_platform,raw_payload", card_ids)}


def stats_breakdowns(base_dir: Path) -> list[StatsBreakdownRow]:
    """Read only the latest breakdown facts needed by statistics and stages."""
    with InfoOSRepository(base_dir).connect_existing() as conn:
        rows = conn.execute("select b.card_id,b.created_at,b.next_step,b.monetization_model,b.risk_flags,"
            "b.replicability,b.credibility,c.source_platform as platform "
            "from opportunity_breakdowns b join cards c on c.card_id=b.card_id where " + LATEST_BREAKDOWN).fetchall()
    return [cast(StatsBreakdownRow, {**dict(row), "risk_flags": json.loads(row["risk_flags"])}) for row in rows]


def breakdown_stages(rows: Sequence[StageBreakdownRow], events: Mapping[str, FollowUpFact],
                     verified: Mapping[str, VerificationFact], include_observe: bool = False, *,
                     now: datetime | None = None, include_unstaged: bool = False) -> dict[str, Stage]:
    """Share board membership and stage derivation without loading display fields."""
    result: dict[str, Stage] = {}
    for row in rows:
        card_id = row["card_id"]
        stage = derive_stage(row["next_step"], row["created_at"], events.get(card_id),
                             verified.get(card_id), include_observe, now=now)
        if stage["stage"] is not None or include_unstaged:
            result[card_id] = stage
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
