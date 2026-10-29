"""Fact-derived opportunity board and complete, chronological card detail."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast

from backend.domain.intelligence.opportunity import CandidateKind, ENUM_MEANINGS
from backend.domain.intelligence.opportunity_follow_up import FOLLOW_UP_LABELS, STAGES, derive_stage
from backend.domain.intelligence.verification import CONCLUSIONS, CREDIBILITY, CONTACT_CHANNELS
from backend.domain.timestamps import parse_utc
from backend.infrastructure.storage.base import InfoOSRepository
from backend.infrastructure.storage.opportunity_follow_ups import OpportunityFollowUpRepository
from backend.read_models._connections import shared_read_connection
from backend.read_models.cards import platform_display_label
from backend.read_models.opportunity_board_data import breakdowns, latest_verifications, verifications
from backend.read_models.opportunity_board_types import (
    BoardColumn, BoardData, BreakdownHistoryRow, OpportunityDetail, OpportunityItem, OpportunityPage, TimelineEntry,
)


def board_items(base_dir: Path, include_observe: bool = False, *, now: datetime | None = None, include_unstaged: bool = False) -> list[OpportunityItem]:
    rows = breakdowns(base_dir)
    events = OpportunityFollowUpRepository(base_dir).latest([row["card_id"] for row in rows])
    records = verifications(base_dir)
    verified, counts = latest_verifications(records), Counter(row["card_id"] for row in records)
    result: list[OpportunityItem] = []
    for row in rows:
        card_id = row["card_id"]
        event, verification = events.get(card_id), verified.get(card_id)
        stage = derive_stage(row["next_step"], row["created_at"], event, verification, include_observe, now=now)
        if stage["stage"] is None and not include_unstaged:
            continue
        result.append({"card_id": card_id, "candidate_kind": row["candidate_kind"], "title": row["title"], "offer": row["offer"],
            "author": row["author"], "platform": row["platform"], "source_name": row["source_name"],
            "source_url": row["source_url"], "published_at": row["published_at"], "author_url": row["author_url"],
            "monetization_model": row["monetization_model"], "replicability": row["replicability"],
            "credibility": row["credibility"], "next_step": row["next_step"], "created_at": row["created_at"],
            **stage,
            "platform_label": platform_display_label(row["platform"]),
            "model_label": ENUM_MEANINGS["monetization_model"][row["monetization_model"]],
            "risk_flags": [risk for risk in row["risk_flags"] if risk != "unverifiable"],
            "risk_labels": [ENUM_MEANINGS["risk_flags"][risk] for risk in row["risk_flags"] if risk != "unverifiable"],
            "question_count": len(row["verify_questions"]), "verification_count": counts[card_id],
            "latest_verification": {"conclusion": verification["conclusion"], "credibility": verification["credibility"],
                "contacted_on": verification["contacted_on"], "doubts": verification["doubts"],
                "created_at": verification["created_at"]} if verification else None,
            "follow_up_reason": event["reason"] if event else None})
    return result


@shared_read_connection
def board(base_dir: Path | str, platform: str | None = None, monetization_model: str | None = None,
          query: str | None = None, include_observe: bool = False, inbox_limit: int = 20,
          inbox_offset: int = 0, candidate_kind: CandidateKind | None = None) -> BoardData:
    base_dir = Path(base_dir)
    now = datetime.now(timezone.utc)
    items = board_items(base_dir, include_observe, now=now)
    platforms = sorted({item["platform"] for item in items})
    filtered = [item for item in items if (platform is None or item["platform"] == platform)
        and (candidate_kind is None or item["candidate_kind"] == candidate_kind)
        and (monetization_model is None or item["monetization_model"] == monetization_model)
        and (query is None or query.casefold() in " ".join(
            str(item[field]) for field in ("card_id", "title", "offer", "author", "source_name") if item[field] is not None).casefold())]
    columns: dict[str, BoardColumn] = {}
    for stage in STAGES:
        group = [item for item in filtered if item["stage"] == stage]
        if stage == "inbox":
            group.sort(key=lambda item: (item["replicability"] + item["credibility"],
                parse_utc(item["created_at"]), item["card_id"]), reverse=True)
        else:
            group.sort(key=lambda item: (parse_utc(cast(str, item["stage_since"])), item["card_id"]), reverse=True)
        columns[stage] = {"count": len(group), "items": group[inbox_offset:inbox_offset + inbox_limit]
            if stage == "inbox" else group}
    return {"columns": columns, "today": {
        "inbox": sum(item["stage"] == "inbox" and now - parse_utc(item["created_at"]) < timedelta(days=1)
                     for item in filtered),
        "plan_stale": sum(item["stage"] == "plan" and item["stale"] for item in filtered),
        "contact": columns["contact"]["count"], "action": columns["action"]["count"]},
        "platforms": [{"platform": value, "label": platform_display_label(value)} for value in platforms],
        "models": ENUM_MEANINGS["monetization_model"]}


@shared_read_connection
def all_opportunities(base_dir: Path | str, *, platform: str | None = None,
                      monetization_model: str | None = None, query: str | None = None,
                      next_step: str | None = None, stage: str | None = None,
                      candidate_kind: CandidateKind | None = None, limit: int = 20, offset: int = 0) -> OpportunityPage:
    items = board_items(Path(base_dir), True, include_unstaged=True)
    platforms = sorted({item["platform"] for item in items})
    filtered = [item for item in items if (platform is None or item["platform"] == platform)
        and (monetization_model is None or item["monetization_model"] == monetization_model)
        and (next_step is None or item["next_step"] == next_step)
        and (candidate_kind is None or item["candidate_kind"] == candidate_kind)
        and (stage is None or (item["stage"] is None if stage == "unstaged" else item["stage"] == stage))
        and (query is None or query.casefold() in " ".join(str(item[field])
            for field in ("card_id", "title", "offer", "author", "source_name") if item[field] is not None).casefold())]
    filtered.sort(key=lambda item: (parse_utc(item["created_at"]), item["card_id"]), reverse=True)
    return {"items": filtered[offset:offset + limit], "total": len(filtered), "limit": limit, "offset": offset,
        "platforms": [{"platform": value, "label": platform_display_label(value)} for value in platforms],
        "models": ENUM_MEANINGS["monetization_model"]}


@shared_read_connection
def detail(base_dir: Path | str, card_id: str) -> OpportunityDetail | None:
    base_dir = Path(base_dir)
    rows = breakdowns(base_dir, card_id)
    if not rows:
        return None
    row = rows[0]
    records = verifications(base_dir, card_id)
    events = OpportunityFollowUpRepository(base_dir).for_card(card_id)
    latest = latest_verifications(records).get(card_id)
    stage = derive_stage(row["next_step"], row["created_at"], events[-1] if events else None, latest, True)
    with InfoOSRepository(base_dir).connect_existing() as conn:
        history = [cast(BreakdownHistoryRow, dict(entry)) for entry in conn.execute(
                               "select breakdown_id,created_at,next_step from opportunity_breakdowns "
                               "where card_id=? order by created_at,breakdown_id", (card_id,)).fetchall()]
    timeline: list[TimelineEntry] = [{"id": entry["breakdown_id"], "kind": "breakdown", "created_at": entry["created_at"],
        "summary": "agent 推荐：" + ENUM_MEANINGS["next_step"][entry["next_step"]], "note": None} for entry in history]
    timeline.extend({"id": event["event_id"], "kind": "follow_up", "created_at": event["created_at"],
        "summary": FOLLOW_UP_LABELS[event["state"]], "note": event["reason"]} for event in events)
    timeline.extend({"id": v["verification_id"], "kind": "verification", "created_at": v["created_at"],
        "summary": "核实：" + CONCLUSIONS[v["conclusion"]] + " · " + CREDIBILITY[v["credibility"]] +
            ("（已更正）" if v["superseded"] else ""),
        "note": "；".join(part for part in [CONTACT_CHANNELS[v["contact_channel"]], v["offer"],
            v["price"], v["delivery"], v["doubts"], v["note"]] if part is not None)} for v in records)
    timeline.sort(key=lambda event: (parse_utc(event["created_at"]), event["id"]))
    return {**row, **stage, "platform_label": platform_display_label(row["platform"]),
        "labels": {"monetization_model": ENUM_MEANINGS["monetization_model"][row["monetization_model"]],
            "next_step": ENUM_MEANINGS["next_step"][row["next_step"]]},
        "risk_labels": [ENUM_MEANINGS["risk_flags"][risk] for risk in row["risk_flags"]],
        "timeline": timeline, "latest_verification": latest, "verification_count": len(records)}
