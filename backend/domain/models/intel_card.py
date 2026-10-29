from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CardTrace(BaseModel):
    model_config = ConfigDict(extra="allow")

    raw_item_id: str


class CardScores(BaseModel):
    timeliness_score: int = Field(ge=1, le=5)
    insight_score: int = Field(ge=1, le=5)
    creation_score: int = Field(ge=1, le=5)
    relevance_score: int = Field(ge=1, le=5)


class CardRouting(BaseModel):
    target: str
    reason: str


class IntelCard(BaseModel):
    model_config = ConfigDict(extra="allow")

    card_id: str
    source_platform: str
    source_type: str
    source_id: str | None = None
    source_lane: str | None = None
    source_url: str
    source_author: str | None = None
    discovery_reason: str | None = None
    viewpoint_hint: str | None = None
    source_confidence: str | None = None
    published_at: str | None = None
    captured_at: str
    processed_at: str
    title: str
    normalized_content: str
    summary: str
    workflow_status: str
    trace: CardTrace
