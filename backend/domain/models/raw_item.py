from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RawAuthor(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    handle: str | None = None
    url: str | None = None


class RawTimestamps(BaseModel):
    model_config = ConfigDict(extra="allow")

    published_at: str | None = None
    updated_at: str | None = None


class RawItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    raw_item_id: str
    connector_id: str
    source_platform: str
    source_type: str
    source_url: str
    captured_at: str
    raw_text: str | None = None
    raw_author: RawAuthor | None = None
    raw_timestamps: RawTimestamps | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    snapshot_ref: str | None = None
