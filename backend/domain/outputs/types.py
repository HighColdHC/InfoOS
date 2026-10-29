"""Pydantic bodies that make intelligence artifacts usable outside the queue."""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


NonEmptyText = Annotated[str, Field(min_length=1)]


class EvidenceRef(BaseModel):
    source_id: NonEmptyText | None = None
    card_id: NonEmptyText
    quote: NonEmptyText
    source_url: str | None = None


class DecisionBriefBody(BaseModel):
    signal: NonEmptyText
    why_it_matters: NonEmptyText
    next_action: NonEmptyText
    deadline: NonEmptyText | None = None
    risks: list[NonEmptyText] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(min_length=1)


class ContentPlanBody(BaseModel):
    hook: NonEmptyText
    audience: NonEmptyText
    thesis: NonEmptyText
    argument: list[NonEmptyText] = Field(min_length=1)
    evidence: list[EvidenceRef] = Field(min_length=1)
    outline: list[NonEmptyText] = Field(min_length=1)
    takeaway: NonEmptyText


class OpportunityCardBody(BaseModel):
    problem: NonEmptyText
    target_user: NonEmptyText
    current_alternative: NonEmptyText
    possible_product: NonEmptyText
    pricing_hypothesis: NonEmptyText
    risks: list[NonEmptyText] = Field(min_length=1)
    missing_evidence: list[NonEmptyText] = Field(min_length=1)
    next_validation: NonEmptyText
    evidence: list[EvidenceRef] = Field(min_length=1)
