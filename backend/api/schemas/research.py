"""HTTP request bodies for research runs."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResearchDateRangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: str
    end_date: str
    timezone: str


class ResearchPlatformBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: str
    max_items: int
    sort: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class ResearchRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    keywords: list[str]
    date_range: ResearchDateRangeBody | None = None
    platforms: list[ResearchPlatformBody]
