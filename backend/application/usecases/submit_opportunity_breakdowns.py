"""Atomically save external-agent breakdowns, readable blocks and publication tasks."""
from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.application.tasks.finalization import enqueue_task
from backend.application.tasks.input_versions import card_finalize_input_version
from backend.application.tasks.planner import versioned_task_id
from backend.application.tasks.types import Task, TaskType
from backend.domain.intelligence.opportunity import (
    OPPORTUNITY_CANDIDATE_RULES_VERSION, OPPORTUNITY_LENS_VERSION,
    format_breakdown, validate_breakdown,
)
from backend.domain.provider_recovery import is_opportunity_breakdown
from backend.domain.timestamps import utc_now_micro_z
from backend.infrastructure.storage.base import utc_now
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.opportunity_breakdowns import OpportunityBreakdownRepository
from backend.infrastructure.storage.opportunity_candidates import OpportunityCandidateRepository
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


def submit_opportunity_breakdowns(base_dir: Path, evaluator: str, items: list[dict[str, Any]], *, token_id: str):
    if not isinstance(evaluator, str) or not 1 <= len(evaluator.strip()) <= 200:
        raise ValueError("invalid_evaluator")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError("invalid_items")
    results = []
    for item in items:
        card_id = item.get("card_id") if isinstance(item, dict) else None
        try:
            if not isinstance(card_id, str) or not card_id.strip():
                raise ValueError("card_id_required")
            results.append(_submit(base_dir, evaluator.strip(), card_id.strip(), item, token_id))
        except ValueError as exc:
            code = str(exc)
            results.append({"card_id": card_id, "status": "rejected", "reason": code,
                            "error": code, "message": "商机拆解未写入，请检查卡片线索与字段约束"})
    return {"results": results}


def _submit(base_dir, evaluator, card_id, item, token_id):
    cards, blocks = CardRepository(base_dir), CardContentBlockRepository(base_dir)
    with cards.connect() as conn:
        conn.execute("begin immediate")
        card = cards.get_card(card_id, connection=conn)
        if card is None:
            raise ValueError("card_missing")
        context = OpportunityCandidateRepository.context(conn, card_id)
        if context is None or context["candidate_kind"] is None:
            raise ValueError("not_opportunity_candidate")
        profile = InterestProfileRepository(base_dir).get(connection=conn)
        if profile is None:
            raise ValueError("profile_required")
        profile_version = item.get("profile_version")
        if type(profile_version) is not int or profile_version < 1:
            raise ValueError("invalid_profile_version")
        if profile_version != profile["profile_version"]:
            raise ValueError("profile_version_mismatch")
        result = validate_breakdown(item)
        breakdown_id = "breakdown_" + sha256(
            f"{card_id}:{card['version']}:{OPPORTUNITY_LENS_VERSION}".encode()).hexdigest()[:32]
        OpportunityBreakdownRepository.upsert(conn, {
            **result, "breakdown_id": breakdown_id, "card_id": card_id, "card_version": card["version"],
            "lens_version": OPPORTUNITY_LENS_VERSION, "rules_version": OPPORTUNITY_CANDIDATE_RULES_VERSION,
            "evaluator": evaluator, "token_id": token_id, "profile_version": profile_version,
            "raw_item_revision": context["raw_item_revision"], "created_at": utc_now_micro_z(),
        })
        old = blocks.list_for_card(card_id, connection=conn)
        blocks.delete_blocks([b["block_id"] for b in old if is_opportunity_breakdown(b)], connection=conn)
        remaining = [b for b in old if not is_opportunity_breakdown(b)]
        position = max((int(b.get("position") or 0) for b in remaining), default=0) + 1
        blocks.upsert_block({
            "block_id": f"{breakdown_id}_block", "card_id": card_id, "position": position,
            "layer": "intelligence", "block_kind": "opportunity_breakdown", "status": "enriched",
            "body": format_breakdown(result), "provenance": "intelligence.opportunity_breakdown",
            "raw_payload": {"purpose": "intelligence.opportunity_breakdown", "lens_version": OPPORTUNITY_LENS_VERSION,
                            "rules_version": OPPORTUNITY_CANDIDATE_RULES_VERSION, "evaluator": evaluator,
                            "profile_version": profile_version, "raw_item_revision": context["raw_item_revision"]},
        }, connection=conn)
        version = card_finalize_input_version(card, blocks.list_for_card(card_id, connection=conn),
            CardAssetRepository(base_dir).list_for_card(card_id, connection=conn))
        version = f"opportunity:{card['version']}:{version}"
        # The planner truncates its version argument; hash the prefixed value first.
        task_id = versioned_task_id(TaskType.CARD_FINALIZE, card_id, sha256(version.encode()).hexdigest())
        enqueue_task(conn, Task(task_id=task_id, task_type=TaskType.CARD_FINALIZE,
            target_kind="card", target_id=card_id, input_version=version,
            artifacts_summary={"card_id": card_id, "opportunity_breakdown_finalize": True}), now=utc_now())
    return {"card_id": card_id, "status": "accepted", "breakdown_id": breakdown_id, "finalize_task_id": task_id}
