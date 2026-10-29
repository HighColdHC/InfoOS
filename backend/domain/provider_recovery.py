"""Block identities shared by agent evaluation and opportunity writeback."""
from __future__ import annotations

from typing import Any


def is_opportunity_breakdown(block: dict[str, Any]) -> bool:
    payload = block.get("raw_payload") or {}
    return (block.get("block_kind") == "opportunity_breakdown"
            or payload.get("purpose") == "intelligence.opportunity_breakdown"
            or block.get("provenance") == "intelligence.opportunity_breakdown")


def is_opportunity_verification(block: dict[str, Any]) -> bool:
    payload = block.get("raw_payload") or {}
    return (block.get("block_kind") == "opportunity_verification"
            or payload.get("purpose") == "user_verified.via_agent"
            or block.get("provenance") == "user_verified.via_agent")


def is_intelligence_evaluation(block: dict[str, Any]) -> bool:
    """Only evaluation blocks may be replaced by evaluation writeback."""
    if is_opportunity_breakdown(block) or is_opportunity_verification(block):
        return False
    payload = block.get("raw_payload") or {}
    purposes = {"intelligence.evaluate", "intelligence.agent_evaluation"}
    return (payload.get("purpose") in purposes or block.get("provenance") in purposes
            or block.get("block_kind") in {"intel_summary", "relation_note"}
            and block.get("layer") == "intelligence")
