from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NormalizedItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    raw_item_id: str
    source_url: str
    title: str
    normalized_content: str
    summary: str
    published_at: str | None = None
    source_author: str | None = None
    content_length: int = 0
    topics: list[str] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
