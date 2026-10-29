"""Atomically store each external-agent evaluation and its publication task."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from backend.application.tasks.finalization import enqueue_task
from backend.application.tasks.input_versions import card_finalize_input_version
from backend.application.tasks.planner import versioned_task_id
from backend.application.tasks.types import Task, TaskType
from backend.domain.intelligence.evaluation import validate_output
from backend.domain.intelligence.knowledge import freshness
from backend.domain.intelligence.triage import validate_triage
from backend.domain.intelligence.rubric import QUALITY_THRESHOLD, RUBRIC_VERSION
from backend.domain.timestamps import utc_now_micro_z
from backend.domain.provider_recovery import is_intelligence_evaluation
from backend.infrastructure.storage.base import utc_now
from backend.infrastructure.storage.card_evaluations import CardEvaluationRepository
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.repositories import CardAssetRepository, CardContentBlockRepository, CardRepository


def submit_evaluations(base_dir: Path, evaluator: str, items: list[dict[str, Any]], *, token_id: str) -> dict[str, Any]:
    if not isinstance(evaluator, str) or not evaluator.strip() or len(evaluator) > 200:
        raise ValueError("evaluator must be nonblank and at most 200 characters")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        raise ValueError("items must contain 1–20 evaluations")
    results = []
    for item in items:
        card_id = item.get("card_id") if isinstance(item, dict) else None
        try:
            if not isinstance(card_id, str) or not card_id.strip():
                raise ValueError("card_id_required")
            results.append(_submit(base_dir, evaluator.strip(), item, token_id))
        except ValueError as exc:
            results.append({"card_id": card_id, "status": "rejected", "reason": str(exc)})
    return {"results": results}


def _submit(base_dir: Path, evaluator: str, item: dict[str, Any], token_id: str) -> dict[str, Any]:
    card_id = item["card_id"]
    cards, blocks = CardRepository(base_dir), CardContentBlockRepository(base_dir)
    with cards.connect() as conn:
        conn.execute("begin immediate")
        card = cards.get_card(card_id, connection=conn)
        if card is None:
            raise ValueError("card_missing")
        profile = InterestProfileRepository(base_dir).get(connection=conn)
        if profile is None:
            raise ValueError("profile_missing")
        result = validate_triage(validate_output(item, profile), is_fresh=
            freshness(card["published_at"], card["first_seen_at"], utc_now_micro_z()) == "fresh")
        evaluation_id = "evaluation_" + sha256(
            f"{card_id}:{card['version']}:{profile['profile_version']}:{RUBRIC_VERSION}".encode()
        ).hexdigest()[:32]
        passed = result["quality"] >= QUALITY_THRESHOLD
        CardEvaluationRepository.insert(conn, {
            **result, "evaluation_id": evaluation_id, "card_version": card["version"],
            "profile_version": profile["profile_version"], "prompt_version": RUBRIC_VERSION,
            "model": evaluator, "provider_id": f"agent:{token_id}", "receipt_id": None,
            "passed_quality": passed, "created_at": utc_now_micro_z(),
        }, overwrite=True)
        old_blocks = blocks.list_for_card(card_id, connection=conn)
        blocks.delete_blocks([b["block_id"] for b in old_blocks if is_intelligence_evaluation(b)], connection=conn)
        if passed:
            position = min((int(b.get("position") or 0) for b in old_blocks if not is_intelligence_evaluation(b)), default=0) - 2
            for offset, kind in enumerate(("intel_summary", "relation_note")):
                blocks.upsert_block({
                    "block_id": f"{evaluation_id}_{kind}", "card_id": card_id,
                    "position": position + offset, "layer": "intelligence", "block_kind": kind,
                    "status": "enriched", "body": result[kind if kind == "relation_note" else "summary"],
                    "provenance": "intelligence.agent_evaluation",
                    "raw_payload": {"purpose": "intelligence.agent_evaluation", "rubric_version": RUBRIC_VERSION,
                                    "profile_version": profile["profile_version"], "evaluator": evaluator},
                }, connection=conn)
        version = card_finalize_input_version(card, blocks.list_for_card(card_id, connection=conn),
            CardAssetRepository(base_dir).list_for_card(card_id, connection=conn))
        # Prefix separates this publication from ordinary pipeline finalization.
        # Card version prevents a later A -> B -> A edit from deduping against an old terminal task.
        version = f"agent:{card['version']}:{version}"
        task_id = versioned_task_id(TaskType.CARD_FINALIZE, card_id, version)
        enqueue_task(conn, Task(task_id=task_id, task_type=TaskType.CARD_FINALIZE,
            target_kind="card", target_id=card_id, input_version=version,
            artifacts_summary={"card_id": card_id, "agent_evaluation_finalize": True}), now=utc_now())
    normalized = {key: result[key] for key in (
        "attribution_kind", "attribution_ref", "relevance", "paused_landmark")}
    warning_codes = {
        "attribution_ref": "attribution_ref_not_in_profile",
        "attribution_kind": "attribution_kind_normalized",
        "relevance": "relevance_normalized",
        "paused_landmark": "paused_landmark_cleared",
    }
    submitted = {**item, "attribution_ref": item.get("attribution_ref") or None}
    warnings = [code for key, code in warning_codes.items() if submitted.get(key) != result[key]]
    return {"card_id": card_id, "status": "accepted", "evaluation_id": evaluation_id,
            "passed_quality": passed, "finalize_task_id": task_id, "normalized": normalized,
            **({"warnings": warnings} if warnings else {})}
