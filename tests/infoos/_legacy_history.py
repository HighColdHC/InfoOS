"""Seed persisted historical products for retained legacy read contracts."""

from backend.infrastructure.storage.base import InfoOSRepository, dumps


def seed_legacy_card_products(base_dir, card_id: str, *, route: str = "business_opportunity") -> str:
    timestamp = "2026-07-02T02:00:00Z"
    seed_legacy_row(base_dir, "semantic_annotations", {
        "annotation_id": f"semantic_{card_id}_history",
        "target_kind": "card", "target_id": card_id,
        "labels": ["business", "content_angle", "trend"],
        "entities": [], "insight": "A durable source points to a useful opportunity.",
        "confidence": 0.95, "evidence_refs": [{"card_id": card_id, "quote": "workflow pain"}],
        "generated_at": timestamp, "model": "historical", "prompt_version": "semantic-v1",
    })
    seed_legacy_row(base_dir, "value_scores", {
        "score_id": f"score_{card_id}_history", "target_kind": "card", "target_id": card_id,
        "scores": {"relevance": 5, "business_potential": 5, "content_potential": 5, "timeliness": 5, "insight": 5},
        "score_reasons": {}, "generated_at": timestamp,
    })
    decision_id = f"decision_{card_id}_history"
    seed_legacy_row(base_dir, "decision_records", {
        "decision_id": decision_id, "target_kind": "card", "target_id": card_id,
        "route": route, "reason": "Historical decision based on durable source evidence.",
        "evidence_refs": [{"card_id": card_id}], "status": "open",
        "created_at": timestamp, "updated_at": timestamp,
    })
    if route == "content_candidate":
        artifact_type = "content_plan"
        body = {
            "hook": "A clear hook", "audience": "Builders",
            "thesis": "Workflow pain suggests a useful product.",
            "argument": ["Review the source evidence."],
            "evidence": [{"card_id": card_id, "quote": "workflow pain"}],
            "outline": ["Explain the source", "Show the opportunity"],
            "takeaway": "Draft a concise post.",
        }
    else:
        artifact_type = "opportunity_card"
        body = {
            "problem": "Workflow pain", "target_user": "Builders",
            "current_alternative": "Manual steps", "possible_product": "A better workflow",
            "pricing_hypothesis": "Monthly subscription", "risks": ["Adoption is uncertain"],
            "missing_evidence": ["Customer interviews"], "next_validation": "Interview users",
            "evidence": [{"card_id": card_id, "quote": "workflow pain"}],
        }
    seed_legacy_row(base_dir, "output_artifacts", {
        "artifact_id": f"output_{card_id}_history", "artifact_type": artifact_type,
        "source_decision_id": decision_id, "title": "Historical output",
        "body": body, "status": "draft", "updated_at": timestamp,
    })
    return decision_id


def seed_legacy_row(base_dir, table: str, payload: dict) -> None:
    """Seed historical rows only in test databases; schema defaults fill omitted fields."""
    values = {key: dumps(value) if isinstance(value, (dict, list)) else value
              for key, value in payload.items()}
    timestamp = "2026-07-02T02:00:00Z"
    if table in {"semantic_annotations", "value_scores"}:
        values.setdefault("generated_at", timestamp)
    else:
        values.setdefault("created_at", timestamp)
        values.setdefault("updated_at", timestamp)
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with InfoOSRepository(base_dir).connect() as conn:
        conn.execute(f"insert into {table} ({columns}) values ({placeholders})", tuple(values.values()))


def seed_legacy_domain(base_dir, name: str, *, sort_order: int = 0) -> str:
    domain_id = "domain_" + name.strip().lower().replace(" ", "_")
    seed_legacy_row(base_dir, "domains", {"domain_id": domain_id, "name": name, "sort_order": sort_order})
    return domain_id


def seed_legacy_insight(base_dir, card_id: str, payload: dict) -> None:
    """Prepare historical insight data for existing read/transition tests."""
    values = {"card_id": card_id, "generated_at": "2026-07-02T02:00:00Z", **payload}
    values = {key: dumps(value) if isinstance(value, (dict, list)) else value
              for key, value in values.items()}
    with InfoOSRepository(base_dir).connect() as conn:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(f"{key}=excluded.{key}" for key in values if key != "card_id")
        conn.execute(f"insert into card_ai_insights ({columns}) values ({placeholders}) "
                     f"on conflict(card_id) do update set {updates}", tuple(values.values()))
        if values.get("input_version") and values.get("status", "succeeded") == "succeeded":
            conn.execute(f"insert into card_ai_insight_versions ({columns}) values ({placeholders}) "
                         f"on conflict(card_id, input_version) do update set {updates}", tuple(values.values()))
