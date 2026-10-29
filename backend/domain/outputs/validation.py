"""Validate an artifact body without depending on persistence or LLM clients."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from backend.domain.outputs.types import ContentPlanBody, DecisionBriefBody, OpportunityCardBody


_BODY_MODELS: dict[str, type[BaseModel]] = {
    "decision_brief": DecisionBriefBody,
    "content_plan": ContentPlanBody,
    "opportunity_card": OpportunityCardBody,
}


def output_body_schema(artifact_type: str) -> type[BaseModel]:
    try:
        return _BODY_MODELS[artifact_type]
    except KeyError as exc:
        raise ValueError(f"unknown_output_artifact_type:{artifact_type}") from exc


def output_body_schema_info(artifact_type: str) -> dict[str, str]:
    """Small stable descriptor for API clients; the full schema remains code-owned."""
    return {"name": output_body_schema(artifact_type).__name__, "version": "output-body-v1"}


def validate_output_body(artifact_type: str, body: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized valid body or a concise ``ValueError``.

    A plain list of desired field names cannot satisfy these schemas: every
    required usable section and at least one evidence reference is required.
    """
    if not isinstance(body, dict):
        raise ValueError(f"invalid_output_body:{artifact_type}: body must be an object")
    try:
        return output_body_schema(artifact_type).model_validate(body).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError(f"invalid_output_body:{artifact_type}: {exc}") from exc


def output_validation_errors(artifact_type: str, body: Any) -> list[str]:
    try:
        validate_output_body(artifact_type, body)
    except ValueError as exc:
        return [str(exc)]
    return []
