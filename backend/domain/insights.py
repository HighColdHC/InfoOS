"""Pure validity and input-version rules for card Summary insights."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


class InvalidCardInsightError(ValueError):
    """A Summary insight is not a valid product."""


@dataclass(frozen=True)
class SummaryPromptSnapshot:
    title: str
    content: str
    existing_summary: str
    enriched_context: str
    context_gaps: str
    visible_domain_names: tuple[str, ...]
    interpretation_version: str
    interpretation_prompt: str
    model: str
    prompt_version: str
    fallback_scores: tuple[tuple[str, int], ...]


def validate_insight_core(main_point: Any, why_it_matters: Any) -> tuple[str, str]:
    main = str(main_point or "").strip()
    why = str(why_it_matters or "").strip()
    if not main:
        raise InvalidCardInsightError("invalid_card_insight:main_point_required")
    if not why:
        raise InvalidCardInsightError("invalid_card_insight:why_it_matters_required")
    return main, why


def is_valid_insight_row(row: Mapping[str, Any] | None) -> bool:
    if row is None or row.get("status") != "succeeded":
        return False
    try:
        validate_insight_core(row.get("main_point"), row.get("why_it_matters"))
    except InvalidCardInsightError:
        return False
    return True


def summary_snapshot_version(snapshot: SummaryPromptSnapshot) -> str:
    encoded = json.dumps(
        asdict(snapshot), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
