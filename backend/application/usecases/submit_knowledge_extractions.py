"""One extraction is one transaction, including every derived card and evidence."""
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.domain.intelligence.knowledge import QuotesValidationError, validate_knowledge
from backend.domain.timestamps import normalize_timestamp, parse_utc, utc_now_micro_z
from backend.infrastructure.storage.interest_profiles import InterestProfileRepository
from backend.infrastructure.storage.knowledge_cards import KnowledgeCardRepository
from backend.infrastructure.storage.knowledge_queries import KnowledgeQueryRepository
from backend.infrastructure.storage.repositories import CardContentBlockRepository, CardRepository


def submit_knowledge_extractions(base_dir: Path, evaluator: str, items: list[dict[str, Any]], *, token_id: str) -> dict[str, Any]:
    if not isinstance(evaluator, str) or not 1 <= len(evaluator.strip()) <= 200:
        raise ValueError("invalid_evaluator")
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        raise ValueError("invalid_items")
    results = []
    for item in items:
        card_id = item.get("card_id") if isinstance(item, dict) else None
        try:
            if not isinstance(card_id, str) or not card_id.strip():
                raise ValueError("card_id_required")
            results.append(_submit(base_dir, evaluator.strip(), item, token_id))
        except ValueError as exc:
            rejected = {"card_id": card_id, "status": "rejected", "reason": str(exc)}
            if isinstance(exc, QuotesValidationError):
                rejected["detail"] = exc.detail
            results.append(rejected)
    return {"results": results}


def _submit(base_dir: Path, evaluator: str, item: dict[str, Any], token_id: str) -> dict[str, Any]:
    repo = KnowledgeQueryRepository(base_dir)
    card_id = item["card_id"]
    now = parse_utc(utc_now_micro_z()).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with repo.connect() as conn:
        conn.execute("begin immediate")
        profile = InterestProfileRepository(base_dir).get(connection=conn)
        if profile is None or type(item.get("profile_version")) is not int or item["profile_version"] != profile["profile_version"]:
            raise ValueError("profile_version_mismatch")
        context = repo.context(conn, card_id, now)
        if context is None:
            raise ValueError("not_extraction_candidate")
        outcome = item.get("outcome")
        nothing_reason = None
        validated = []
        if outcome == "nothing":
            nothing_reason = item.get("nothing_reason")
            if not isinstance(nothing_reason, str) or not 1 <= len(nothing_reason.strip()) <= 200:
                raise ValueError("invalid_nothing_reason")
            nothing_reason = nothing_reason.strip()
        elif outcome == "extracted":
            cards = item.get("cards")
            if not isinstance(cards, list) or not 1 <= len(cards) <= 8:
                raise ValueError("invalid_cards")
            original = CardRepository(base_dir).get_card(card_id, connection=conn)
            texts = [original["content"], *[block["body"] for block in
                CardContentBlockRepository(base_dir).list_for_card(card_id, connection=conn)]]
            seen = set()
            for index, value in enumerate(cards):
                if not isinstance(value, dict):
                    raise ValueError("invalid_knowledge_card")
                target_id = value.get("target_knowledge_id")
                if value.get("action") != "new" and not isinstance(target_id, str):
                    raise ValueError("invalid_merge_target")
                target = repo.get(conn, target_id) if isinstance(target_id, str) else None
                try:
                    result = validate_knowledge(value, profile, is_fresh=context["freshness"] == "fresh", texts=texts, target=target)
                except QuotesValidationError as exc:
                    raise QuotesValidationError(str(exc), f"cards[{index}].{exc.detail}") from exc
                knowledge_id = "kc_" + uuid4().hex if result["action"] == "new" else target_id
                if knowledge_id in seen or conn.execute("select 1 from knowledge_evidence where knowledge_id=? and card_id=?",
                                                         (knowledge_id, card_id)).fetchone() is not None:
                    raise ValueError("duplicate_evidence")
                seen.add(knowledge_id)
                validated.append((knowledge_id, result))
        else:
            raise ValueError("invalid_outcome")
        extraction_id = KnowledgeCardRepository.extraction(conn, card_id=card_id,
            card_version=context["card_version"], profile_version=profile["profile_version"], channel=context["channel"],
            outcome=outcome, nothing_reason=nothing_reason, evaluator=evaluator, token_id=token_id, now=now)
        for knowledge_id, result in validated:
            KnowledgeCardRepository.write(conn, result, knowledge_id=knowledge_id, card_id=card_id,
                extraction_id=extraction_id, profile_version=profile["profile_version"], evaluator=evaluator,
                published_at=normalize_timestamp(context["published_at"]), origin_freshness=context["freshness"], now=now)
    return {"card_id": card_id, "status": "accepted", "extraction_id": extraction_id,
            "cards": [{"knowledge_id": knowledge_id, "kind": value["kind"], "action": value["action"]}
                      for knowledge_id, value in validated]}
