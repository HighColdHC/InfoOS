"""Pure normalization of agent evaluation results."""
from __future__ import annotations
from typing import Any
from backend.domain.intelligence.profile import CONTENT_TYPES, SUGGESTED_ACTIONS


def validate_output(value: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("evaluation_not_object")
    result = dict(value)
    for key in ("quality", "relevance"):
        number = result.get(key)
        if key == "relevance" and number is None and result.get("attribution_kind") == "paused":
            continue
        if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 100:
            raise ValueError(f"evaluation_invalid_{key}")
    kind = result.get("attribution_kind")
    if not isinstance(kind, str) or kind not in {"project", "kr", "question", "domain", "exploration", "paused"}:
        raise ValueError("evaluation_invalid_attribution_kind")
    if (not isinstance(result.get("content_type"), str) or result["content_type"] not in CONTENT_TYPES
            or not isinstance(result.get("suggested_action"), str) or result["suggested_action"] not in SUGGESTED_ACTIONS):
        raise ValueError("evaluation_invalid_enum")
    for key in ("summary", "relation_note"):
        if not isinstance(result.get(key), str) or not result[key].strip():
            raise ValueError(f"evaluation_invalid_{key}")
        result[key] = result[key].strip()
        if len(result[key]) > 500:
            raise ValueError(f"evaluation_{key}_too_long")
    if not isinstance(result.get("paused_landmark"), bool):
        raise ValueError("evaluation_invalid_paused_landmark")
    ref = result.get("attribution_ref")
    ref = ref if isinstance(ref, str) and ref else None
    sections = profile["sections"]
    all_refs = {item for items in sections.values() for item in items}
    if ref not in all_refs:
        if result["relevance"] is None:
            result["relevance"] = 0
        result["attribution_ref"] = None
        result["attribution_kind"] = "exploration" if kind == "exploration" or (result["relevance"] is not None and result["relevance"] < 40) else "domain"
    else:
        result["attribution_ref"] = ref
        if ref in sections["暂放方向"]:
            result["attribution_kind"] = "paused"
        elif kind == "paused":
            result["attribution_kind"] = "domain"
    if result["attribution_kind"] == "paused":
        result["relevance"] = None
    else:
        result["paused_landmark"] = False
        if result["relevance"] is None:
            raise ValueError("evaluation_invalid_relevance")
    return result
