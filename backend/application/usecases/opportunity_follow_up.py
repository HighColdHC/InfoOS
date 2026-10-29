"""Persist a user decision and return the resulting fact-derived stage."""
from pathlib import Path
from uuid import uuid4

from backend.domain.intelligence.opportunity_follow_up import derive_stage
from backend.domain.timestamps import parse_utc, utc_now_micro_z
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository
from backend.infrastructure.storage.opportunity_follow_ups import FollowUpEvent, OpportunityFollowUpRepository
from backend.infrastructure.storage.opportunity_verifications import OpportunityVerificationRepository
from backend.infrastructure.storage.repositories import CardRepository


def set_opportunity_follow_up(base_dir: Path, card_id: str, state: str, reason: str | None = None,
                              *, actor: str) -> dict:
    # HTTP and MCP share this request-boundary validation.
    if state not in {"following", "dismissed", "reset"}:
        raise ValueError("invalid_follow_up_state")
    if reason is not None:
        reason = reason.strip()
        if len(reason) > 200:
            raise ValueError("follow_up_reason_too_long")
    events = OpportunityFollowUpRepository(base_dir)
    with events.connect() as conn:
        conn.execute("begin immediate")
        if CardRepository(base_dir).get_card(card_id, connection=conn) is None:
            raise ValueError("card_not_found")
        breakdown = OpportunityBreakdownRepository(base_dir).latest_for_card(card_id, connection=conn)
        if breakdown is None:
            raise ValueError("opportunity_breakdown_required")
        row = OpportunityVerificationRepository.latest_conclusion(conn, card_id)
        verification = dict(row) if row is not None else None
        previous = events.latest([card_id], connection=conn).get(card_id)
        current = previous is not None and (verification is None or
            parse_utc(previous["created_at"]) > parse_utc(verification["created_at"]))
        if current and previous["state"] == state:
            event = previous
        else:
            event: FollowUpEvent = {"event_id": "follow_up_" + uuid4().hex, "card_id": card_id,
                "state": state, "reason": reason, "actor": actor, "created_at": utc_now_micro_z()}
            events.append(conn, event)
        stage = derive_stage(breakdown["next_step"], breakdown["created_at"], event, verification)
        return {"stage": stage["stage"], "stage_since": stage["stage_since"], "event_id": event["event_id"]}
